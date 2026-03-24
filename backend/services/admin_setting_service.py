from __future__ import annotations

from backend.models.orm import AdminSettingORM
from backend.repositories.admin_setting_repo import AdminSettingRepository


class AdminSettingNotFoundError(Exception):
    pass


class AdminSettingService:
    YOOKASSA_KEYS = (
        "yookassa_shop_id",
        "yookassa_secret_key",
        "yookassa_mode",
    )

    def __init__(self, setting_repo: AdminSettingRepository) -> None:
        self.setting_repo = setting_repo

    def list_settings(self) -> list[AdminSettingORM]:
        return self.setting_repo.list_all()

    def get_setting(self, key: str) -> AdminSettingORM:
        setting = self.setting_repo.get_by_key(key)
        if setting is None:
            raise AdminSettingNotFoundError(f"Admin setting '{key}' was not found.")
        return setting

    def upsert_setting(
        self,
        key: str,
        value: str | None,
        *,
        is_secret: bool | None = None,
        updated_by_telegram_id: int | None = None,
    ) -> AdminSettingORM:
        setting = self.setting_repo.get_by_key(key)
        if setting is None:
            return self.setting_repo.create(
                key=key,
                value=value,
                is_secret=bool(is_secret),
                updated_by_telegram_id=updated_by_telegram_id,
            )

        setting.value = value
        if is_secret is not None:
            setting.is_secret = bool(is_secret)
        if updated_by_telegram_id is not None:
            setting.updated_by_telegram_id = updated_by_telegram_id
        return self.setting_repo.save(setting)

    def get_yookassa_settings(self) -> dict[str, object]:
        values: dict[str, object] = {}
        for key in self.YOOKASSA_KEYS:
            setting = self.setting_repo.get_by_key(key)
            values[key] = self.setting_to_dict(setting) if setting is not None else None
        return values

    def get_value(self, key: str, default: str = "") -> str:
        setting = self.setting_repo.get_by_key(key)
        if setting is None or setting.value is None:
            return default
        return setting.value

    def ensure_default_yookassa_settings(self) -> None:
        defaults = {
            "yookassa_shop_id": {"value": None, "is_secret": False},
            "yookassa_secret_key": {"value": None, "is_secret": True},
            "yookassa_mode": {"value": "test", "is_secret": False},
        }
        for key, values in defaults.items():
            if self.setting_repo.get_by_key(key) is not None:
                continue
            self.setting_repo.create(
                key=key,
                value=values["value"],
                is_secret=bool(values["is_secret"]),
            )

    def update_yookassa_settings(
        self,
        payload: dict[str, object],
        *,
        updated_by_telegram_id: int | None = None,
    ) -> dict[str, object]:
        result: dict[str, object] = {}
        for key in self.YOOKASSA_KEYS:
            if key not in payload:
                setting = self.setting_repo.get_by_key(key)
                result[key] = self.setting_to_dict(setting) if setting is not None else None
                continue
            value = payload.get(key)
            setting = self.upsert_setting(
                key,
                None if value is None else str(value),
                is_secret=(key == "yookassa_secret_key"),
                updated_by_telegram_id=updated_by_telegram_id,
            )
            result[key] = self.setting_to_dict(setting)
        return result

    def setting_to_dict(self, setting: AdminSettingORM) -> dict[str, object]:
        return {
            "id": setting.id,
            "key": setting.key,
            "value": setting.value,
            "is_secret": setting.is_secret,
            "updated_by_telegram_id": setting.updated_by_telegram_id,
        }
