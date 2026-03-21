from __future__ import annotations

from datetime import datetime, timedelta
from secrets import token_urlsafe


def utc_now() -> datetime:
    return datetime.utcnow()


def days_from_now(days: int) -> datetime:
    return utc_now() + timedelta(days=days)


def isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def generate_secret(prefix: str) -> str:
    return f"{prefix}_{token_urlsafe(18)}"


def generate_payment_code() -> str:
    return generate_secret("pay")[:10]
