from __future__ import annotations

from backend.models.orm import UserORM
from shared.postgres import SessionLocal


class UserRepository:

    def __init__(self) -> None:
        pass

    def _get_db(self):
        return SessionLocal()

    def create(
        self,
        telegram_id: int,
        username: str | None,
        role: str = "user",
        phone: str | None = None,
    ) -> UserORM:
        db = self._get_db()
        try:
            user = UserORM(
                telegram_id=telegram_id,
                username=username,
                role=role,
                phone=phone,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            return user
        finally:
            db.close()

    def save(self, user: UserORM) -> UserORM:
        db = self._get_db()
        try:
            merged_user = db.merge(user)
            db.commit()
            db.refresh(merged_user)
            return merged_user
        finally:
            db.close()

    def get(self, user_id: int) -> UserORM | None:
        db = self._get_db()
        try:
            return db.query(UserORM).filter_by(id=user_id).first()
        finally:
            db.close()

    def get_by_telegram_id(self, telegram_id: int) -> UserORM | None:
        db = self._get_db()
        try:
            return db.query(UserORM).filter_by(telegram_id=telegram_id).first()
        finally:
            db.close()

    def list_all(self) -> list[UserORM]:
        db = self._get_db()
        try:
            return db.query(UserORM).all()
        finally:
            db.close()
