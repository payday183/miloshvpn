from datetime import timedelta
from math import ceil

from backend.models.orm import SubscriptionORM
from shared.postgres import SessionLocal
from shared.utils import utc_now


class SubscriptionNotFoundError(Exception):
    pass


class NoActiveSubscriptionError(Exception):
    pass


class SubscriptionService:
    def __init__(self, default_duration_days: int = 30):
        self.default_duration_days = default_duration_days

    def _get_db(self):
        return SessionLocal()

    def get_latest_subscription(self, user_id: int):
        db = self._get_db()
        try:
            return (
                db.query(SubscriptionORM)
                .filter_by(user_id=user_id)
                .order_by(SubscriptionORM.id.desc())
                .first()
            )
        finally:
            db.close()

    def get_active_subscription(self, user_id: int):
        db = self._get_db()
        try:
            return (
                db.query(SubscriptionORM)
                .filter(SubscriptionORM.user_id == user_id)
                .filter(SubscriptionORM.status == "active")
                .filter(SubscriptionORM.ends_at > utc_now())
                .order_by(SubscriptionORM.id.desc())
                .first()
            )
        finally:
            db.close()

    def require_active_subscription(self, user_id: int):
        sub = self.get_active_subscription(user_id)
        if not sub:
            raise NoActiveSubscriptionError()
        return sub

    def activate_subscription(self, user_id: int, plan: str, days: int, source_payment_id=None):
        now = utc_now()
        db = self._get_db()
        try:
            existing = (
                db.query(SubscriptionORM)
                .filter(SubscriptionORM.user_id == user_id)
                .filter(SubscriptionORM.status == "active")
                .filter(SubscriptionORM.ends_at > now)
                .order_by(SubscriptionORM.id.desc())
                .first()
            )

            if existing:
                existing.plan = plan
                existing.ends_at = existing.ends_at + timedelta(days=days)
                if source_payment_id is not None:
                    existing.source_payment_id = source_payment_id
                db.commit()
                db.refresh(existing)
                return existing

            sub = SubscriptionORM(
                user_id=user_id,
                plan=plan,
                status="active",
                started_at=now,
                ends_at=now + timedelta(days=days),
                source_payment_id=source_payment_id,
            )

            db.add(sub)
            db.commit()
            db.refresh(sub)
            return sub
        finally:
            db.close()

    def expire_overdue_subscriptions(self):
        now = utc_now()
        db = self._get_db()
        try:
            subs = db.query(SubscriptionORM).filter_by(status="active").all()

            expired = []
            for sub in subs:
                if sub.ends_at <= now:
                    sub.status = "expired"
                    expired.append(sub)

            db.commit()
            for sub in expired:
                db.refresh(sub)
            return expired
        finally:
            db.close()

    def list_subscriptions(self):
        db = self._get_db()
        try:
            return db.query(SubscriptionORM).all()
        finally:
            db.close()

    def get_days_left(self, subscription: SubscriptionORM | None) -> int | None:
        if subscription is None or subscription.ends_at is None:
            return None
        remaining_seconds = (subscription.ends_at - utc_now()).total_seconds()
        if remaining_seconds <= 0:
            return 0
        return int(ceil(remaining_seconds / 86400))
