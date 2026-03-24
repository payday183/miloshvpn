from __future__ import annotations

import hashlib
import hmac
import json
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qsl

from backend.config import settings
from backend.models.orm import UserORM, WebAppSessionORM
from backend.repositories.webapp_session_repo import WebAppSessionRepository
from backend.services.user_service import PermissionDeniedError, UserService
from shared.utils import generate_secret, utc_now


class InvalidTelegramAuthError(Exception):
    pass


class WebAppSessionNotFoundError(Exception):
    pass


class WebAppAuthService:
    def __init__(
        self,
        user_service: UserService,
        session_repo: WebAppSessionRepository,
        session_ttl_seconds: int = 86400,
        auth_max_age_seconds: int = 3600,
    ) -> None:
        self.user_service = user_service
        self.session_repo = session_repo
        self.session_ttl_seconds = session_ttl_seconds
        self.auth_max_age_seconds = auth_max_age_seconds

    def authenticate_telegram_webapp(
        self,
        init_data: str,
        scope: str = "user",
    ) -> tuple[UserORM, WebAppSessionORM]:
        validated = self._validate_init_data(init_data)
        raw_user = validated.get("user")
        if not isinstance(raw_user, dict):
            raise InvalidTelegramAuthError("В Telegram initData нет корректного пользователя.")

        telegram_id = raw_user.get("id")
        if not isinstance(telegram_id, int):
            raise InvalidTelegramAuthError("В Telegram initData нет ID пользователя.")

        username = raw_user.get("username")
        if username is not None and not isinstance(username, str):
            username = None

        user = self.user_service.register_user(
            telegram_id=telegram_id,
            username=username,
        )

        session = self._create_session(
            user=user,
            scope=scope,
            init_data_hash=str(validated.get("hash") or ""),
        )
        return user, session

    def create_browser_session(
        self,
        telegram_id: int,
        username: str | None = None,
        scope: str = "user",
    ) -> tuple[UserORM, WebAppSessionORM]:
        user = self.user_service.register_user(
            telegram_id=telegram_id,
            username=username,
        )
        session = self._create_session(
            user=user,
            scope=scope,
            init_data_hash="browser-link",
        )
        return user, session

    def require_session(
        self,
        session_token: str,
        *,
        required_scope: str | None = None,
    ) -> tuple[UserORM, WebAppSessionORM]:
        session = self.session_repo.get_by_token(session_token)
        if session is None or session.revoked_at is not None:
            raise WebAppSessionNotFoundError("Сессия web app не найдена.")

        now = utc_now()
        if session.expires_at <= now:
            session.revoked_at = now
            self.session_repo.save(session)
            raise WebAppSessionNotFoundError("Сессия web app истекла.")

        if required_scope == "admin" and session.scope != "admin":
            raise PermissionDeniedError("Нужна админ-сессия.")

        user = self.user_service.require_user_by_telegram_id(session.telegram_id)
        if required_scope == "admin" and user.role != "admin":
            raise PermissionDeniedError("Нужна админ-сессия.")

        session.last_seen_at = now
        session = self.session_repo.save(session)
        return user, session

    def _validate_init_data(self, init_data: str) -> dict[str, Any]:
        if not init_data:
            raise InvalidTelegramAuthError("Telegram initData пустой.")
        if not settings.bot_token:
            raise InvalidTelegramAuthError("BOT_TOKEN не настроен.")

        pairs = parse_qsl(init_data, keep_blank_values=True)
        values = dict(pairs)
        received_hash = values.pop("hash", None)
        if not received_hash:
            raise InvalidTelegramAuthError("В Telegram initData нет hash.")

        data_check_string = "\n".join(
            f"{key}={value}"
            for key, value in sorted(values.items(), key=lambda item: item[0])
        )
        secret_key = hmac.new(
            b"WebAppData",
            settings.bot_token.encode(),
            hashlib.sha256,
        ).digest()
        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(calculated_hash, received_hash):
            raise InvalidTelegramAuthError("Hash Telegram initData не совпадает.")

        auth_date = values.get("auth_date")
        if auth_date is None or not str(auth_date).isdigit():
            raise InvalidTelegramAuthError("В Telegram initData нет auth_date.")

        auth_ts = int(str(auth_date))
        age_seconds = int(utc_now().timestamp()) - auth_ts
        if age_seconds < -30:
            raise InvalidTelegramAuthError("Telegram auth_date находится в будущем.")
        if age_seconds > self.auth_max_age_seconds:
            raise InvalidTelegramAuthError("Telegram initData слишком старый.")

        parsed: dict[str, Any] = {"hash": received_hash}
        for key, value in values.items():
            if key in {"user", "chat", "receiver"}:
                try:
                    parsed[key] = json.loads(value)
                except json.JSONDecodeError as error:
                    raise InvalidTelegramAuthError(
                        f"Поле Telegram '{key}' содержит некорректный JSON."
                    ) from error
                continue
            parsed[key] = value
        return parsed

    def _create_session(
        self,
        *,
        user: UserORM,
        scope: str,
        init_data_hash: str | None,
    ) -> WebAppSessionORM:
        if scope == "admin" and user.role != "admin":
            raise PermissionDeniedError("Только админ может открыть эту панель.")

        return self.session_repo.create(
            user_id=user.id,
            telegram_id=user.telegram_id,
            session_token=generate_secret("webapp"),
            scope=scope,
            init_data_hash=init_data_hash,
            expires_at=utc_now() + timedelta(seconds=self.session_ttl_seconds),
        )
