from __future__ import annotations

from datetime import datetime

from backend.models.orm import WebAppSessionORM
from shared.postgres import SessionLocal


class WebAppSessionRepository:
    def _get_db(self):
        return SessionLocal()

    def create(
        self,
        user_id: int | None,
        telegram_id: int,
        session_token: str,
        scope: str,
        init_data_hash: str | None,
        expires_at: datetime,
    ) -> WebAppSessionORM:
        db = self._get_db()
        try:
            session = WebAppSessionORM(
                user_id=user_id,
                telegram_id=telegram_id,
                session_token=session_token,
                scope=scope,
                init_data_hash=init_data_hash,
                expires_at=expires_at,
            )
            db.add(session)
            db.commit()
            db.refresh(session)
            return session
        finally:
            db.close()

    def save(self, session: WebAppSessionORM) -> WebAppSessionORM:
        db = self._get_db()
        try:
            merged = db.merge(session)
            db.commit()
            db.refresh(merged)
            return merged
        finally:
            db.close()

    def get_by_token(self, session_token: str) -> WebAppSessionORM | None:
        db = self._get_db()
        try:
            return db.query(WebAppSessionORM).filter_by(session_token=session_token).first()
        finally:
            db.close()
