import hashlib
import hmac
import time
from typing import Mapping
from urllib.parse import urlencode, urlsplit, urlunsplit

from app.config import get_settings

PROFILE_AUTH_TTL_SECONDS = 15 * 60
TELEGRAM_LOGIN_MAX_AGE_SECONDS = 24 * 60 * 60


def build_admin_profile_url(telegram_id: int) -> str:
    expires_at = int(time.time()) + PROFILE_AUTH_TTL_SECONDS
    payload = f"{telegram_id}:{expires_at}"
    query = urlencode(
        {
            "telegram_id": telegram_id,
            "exp": expires_at,
            "sig": _sign(payload),
        }
    )
    return f"{admin_profile_base_url()}?{query}"


def verify_admin_profile_signature(params: Mapping[str, str]) -> int | None:
    telegram_id = str(params.get("telegram_id") or "")
    expires_at = str(params.get("exp") or "")
    signature = str(params.get("sig") or "")

    if not telegram_id.isdigit() or not expires_at.isdigit() or not signature:
        return None
    if int(expires_at) < int(time.time()):
        return None

    payload = f"{telegram_id}:{expires_at}"
    expected = _sign(payload)
    if not hmac.compare_digest(signature, expected):
        return None
    return int(telegram_id)


def verify_telegram_login(params: Mapping[str, str]) -> int | None:
    settings = get_settings()
    supplied_hash = str(params.get("hash") or "")
    if not settings.bot_token or not supplied_hash:
        return None

    data = {key: str(value) for key, value in params.items() if key != "hash"}
    try:
        auth_date = int(data.get("auth_date") or "0")
    except ValueError:
        return None

    if auth_date < int(time.time()) - TELEGRAM_LOGIN_MAX_AGE_SECONDS:
        return None

    check_string = "\n".join(f"{key}={data[key]}" for key in sorted(data))
    secret_key = hashlib.sha256(settings.bot_token.encode()).digest()
    expected = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(supplied_hash, expected):
        return None

    telegram_id = data.get("id")
    return int(telegram_id) if telegram_id and telegram_id.isdigit() else None


def admin_profile_base_url() -> str:
    settings = get_settings()
    panel_url = settings.admin_panel_url or "http://localhost:8081/admin"
    parts = urlsplit(panel_url)
    path = parts.path.rstrip("/")
    if path.endswith("/admin"):
        path = f"{path}/profile"
    else:
        path = "/admin/profile"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def admin_panel_url_with_token() -> str:
    settings = get_settings()
    panel_url = settings.admin_panel_url or "http://localhost:8081/admin"
    if not settings.admin_web_token:
        return panel_url

    separator = "&" if "?" in panel_url else "?"
    return f"{panel_url}{separator}{urlencode({'token': settings.admin_web_token})}"


def _sign(payload: str) -> str:
    secret = _signing_secret()
    return hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()


def _signing_secret() -> bytes:
    settings = get_settings()
    secret = settings.admin_web_token or settings.bot_token
    return secret.encode()
