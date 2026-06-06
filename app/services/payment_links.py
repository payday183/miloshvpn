from decimal import Decimal
from typing import Mapping
from urllib.parse import urlencode, urlsplit, urlunsplit

from app.config import get_settings
from app.models import Order


def donation_url_for_order(order: Order, amount: Decimal | None = None, *, email: str | None = None) -> str:
    settings = get_settings()
    if not settings.donationalerts_donate_url:
        return ""

    _ = (order, amount, email)
    parts = urlsplit(settings.donationalerts_donate_url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", parts.fragment))


def public_url(path: str, query: Mapping[str, str] | None = None) -> str:
    settings = get_settings()
    base = settings.admin_panel_url or f"http://localhost:{settings.app_port}/admin"
    return build_url(base, path, query)


def build_url(base: str, path: str, query: Mapping[str, str] | None = None) -> str:
    parts = urlsplit(base)
    if not parts.scheme or not parts.netloc:
        settings = get_settings()
        parts = urlsplit(f"http://localhost:{settings.app_port}")
    return urlunsplit((parts.scheme, parts.netloc, path, urlencode(query or {}), ""))


def format_amount(value: Decimal) -> str:
    return str(value.quantize(Decimal("1"))) if value == value.quantize(Decimal("1")) else str(value)
