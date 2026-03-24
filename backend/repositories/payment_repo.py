from __future__ import annotations

from typing import Any

from backend.models.orm import PaymentORM
from shared.postgres import SessionLocal


class PaymentRepository:

    def __init__(self) -> None:
        pass

    def _get_db(self):
        return SessionLocal()

    def create(
        self,
        user_id: int,
        amount: float,
        currency: str = "RUB",
        provider: str = "manual",
        external_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        provider_payment_id: str | None = None,
        provider_status: str | None = None,
        payment_method: str | None = None,
        confirmation_url: str | None = None,
        idempotence_key: str | None = None,
        metadata_json: str | None = None,
        error_message: str | None = None,
    ) -> PaymentORM:
        db = self._get_db()
        try:
            payment = PaymentORM(
                user_id=user_id,
                amount=amount,
                currency=currency,
                status="pending",
                provider=provider,
                external_id=external_id,
                provider_payment_id=provider_payment_id,
                provider_status=provider_status,
                plan=(metadata or {}).get("plan", "month"),
                duration_days=(metadata or {}).get("duration_days"),
                payment_method=payment_method,
                confirmation_url=confirmation_url,
                idempotence_key=idempotence_key,
                metadata_json=metadata_json,
                error_message=error_message,
            )
            db.add(payment)
            db.commit()
            db.refresh(payment)
            return payment
        finally:
            db.close()

    def save(self, payment: PaymentORM) -> PaymentORM:
        db = self._get_db()
        try:
            merged_payment = db.merge(payment)
            db.commit()
            db.refresh(merged_payment)
            return merged_payment
        finally:
            db.close()

    def get(self, payment_id: int) -> PaymentORM | None:
        db = self._get_db()
        try:
            return db.query(PaymentORM).filter_by(id=payment_id).first()
        finally:
            db.close()

    def get_by_code(self, payment_code: str) -> PaymentORM | None:
        db = self._get_db()
        try:
            return db.query(PaymentORM).filter_by(payment_code=payment_code).first()
        finally:
            db.close()

    def get_by_provider_payment_id(self, provider_payment_id: str) -> PaymentORM | None:
        db = self._get_db()
        try:
            return (
                db.query(PaymentORM)
                .filter_by(provider_payment_id=provider_payment_id)
                .first()
            )
        finally:
            db.close()

    def list_for_user(self, user_id: int) -> list[PaymentORM]:
        db = self._get_db()
        try:
            return (
                db.query(PaymentORM)
                .filter_by(user_id=user_id)
                .order_by(PaymentORM.created_at.desc(), PaymentORM.id.desc())
                .all()
            )
        finally:
            db.close()

    def list_recent(self, limit: int = 20) -> list[PaymentORM]:
        db = self._get_db()
        try:
            return (
                db.query(PaymentORM)
                .order_by(PaymentORM.created_at.desc())
                .limit(limit)
                .all()
            )
        finally:
            db.close()
