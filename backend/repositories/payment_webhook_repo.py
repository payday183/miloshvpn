from __future__ import annotations

from backend.models.orm import PaymentWebhookEventORM
from shared.postgres import SessionLocal


class PaymentWebhookEventRepository:
    def _get_db(self):
        return SessionLocal()

    def create(
        self,
        *,
        provider: str,
        event_type: str,
        provider_object_id: str | None,
        payment_id: int | None,
        payload: str,
    ) -> PaymentWebhookEventORM:
        db = self._get_db()
        try:
            event = PaymentWebhookEventORM(
                provider=provider,
                event_type=event_type,
                provider_object_id=provider_object_id,
                payment_id=payment_id,
                payload=payload,
            )
            db.add(event)
            db.commit()
            db.refresh(event)
            return event
        finally:
            db.close()

    def save(self, event: PaymentWebhookEventORM) -> PaymentWebhookEventORM:
        db = self._get_db()
        try:
            merged = db.merge(event)
            db.commit()
            db.refresh(merged)
            return merged
        finally:
            db.close()
