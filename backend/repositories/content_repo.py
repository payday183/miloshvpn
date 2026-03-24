from __future__ import annotations

from backend.models.orm import ContentBlockORM
from shared.postgres import SessionLocal


class ContentRepository:
    def _get_db(self):
        return SessionLocal()

    def list_blocks(self, active_only: bool = False) -> list[ContentBlockORM]:
        db = self._get_db()
        try:
            query = db.query(ContentBlockORM)
            if active_only:
                query = query.filter_by(is_active=True)
            return query.order_by(ContentBlockORM.key.asc()).all()
        finally:
            db.close()

    def get_by_key(self, key: str) -> ContentBlockORM | None:
        db = self._get_db()
        try:
            return db.query(ContentBlockORM).filter_by(key=key).first()
        finally:
            db.close()

    def save(self, block: ContentBlockORM) -> ContentBlockORM:
        db = self._get_db()
        try:
            merged = db.merge(block)
            db.commit()
            db.refresh(merged)
            return merged
        finally:
            db.close()

    def create(
        self,
        key: str,
        body: str,
        title: str | None = None,
        format: str = "markdown",
        is_active: bool = True,
        updated_by_telegram_id: int | None = None,
    ) -> ContentBlockORM:
        db = self._get_db()
        try:
            block = ContentBlockORM(
                key=key,
                title=title,
                body=body,
                format=format,
                is_active=is_active,
                updated_by_telegram_id=updated_by_telegram_id,
            )
            db.add(block)
            db.commit()
            db.refresh(block)
            return block
        finally:
            db.close()
