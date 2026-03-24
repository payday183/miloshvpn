from __future__ import annotations

from backend.models.orm import AdminSettingORM
from shared.postgres import SessionLocal


class AdminSettingRepository:
    def _get_db(self):
        return SessionLocal()

    def list_all(self) -> list[AdminSettingORM]:
        db = self._get_db()
        try:
            return db.query(AdminSettingORM).order_by(AdminSettingORM.key.asc()).all()
        finally:
            db.close()

    def get_by_key(self, key: str) -> AdminSettingORM | None:
        db = self._get_db()
        try:
            return db.query(AdminSettingORM).filter_by(key=key).first()
        finally:
            db.close()

    def save(self, setting: AdminSettingORM) -> AdminSettingORM:
        db = self._get_db()
        try:
            merged = db.merge(setting)
            db.commit()
            db.refresh(merged)
            return merged
        finally:
            db.close()

    def create(
        self,
        key: str,
        value: str | None = None,
        is_secret: bool = False,
        updated_by_telegram_id: int | None = None,
    ) -> AdminSettingORM:
        db = self._get_db()
        try:
            setting = AdminSettingORM(
                key=key,
                value=value,
                is_secret=is_secret,
                updated_by_telegram_id=updated_by_telegram_id,
            )
            db.add(setting)
            db.commit()
            db.refresh(setting)
            return setting
        finally:
            db.close()
