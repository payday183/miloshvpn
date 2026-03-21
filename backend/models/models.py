from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from shared.utils import isoformat, utc_now


@dataclass(slots=True)
class User:
    id: int
    telegram_id: int
    username: str | None = None
    role: str = "user"
    created_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "telegram_id": self.telegram_id,
            "username": self.username,
            "role": self.role,
            "created_at": isoformat(self.created_at),
        }


@dataclass(slots=True)
class Payment:
    id: int
    user_id: int
    amount: float
    currency: str = "RUB"
    status: str = "pending"
    provider: str = "manual"
    external_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "amount": self.amount,
            "currency": self.currency,
            "status": self.status,
            "provider": self.provider,
            "external_id": self.external_id,
            "metadata": dict(self.metadata),
            "created_at": isoformat(self.created_at),
        }


@dataclass(slots=True)
class Subscription:
    id: int
    user_id: int
    plan: str
    status: str = "active"
    started_at: datetime = field(default_factory=utc_now)
    ends_at: datetime = field(default_factory=utc_now)
    source_payment_id: int | None = None

    @property
    def is_active(self) -> bool:
        return self.status == "active" and self.ends_at > utc_now()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "plan": self.plan,
            "status": self.status,
            "started_at": isoformat(self.started_at),
            "ends_at": isoformat(self.ends_at),
            "source_payment_id": self.source_payment_id,
            "is_active": self.is_active,
        }


@dataclass(slots=True)
class VPNServer:
    name: str
    host: str
    port: int
    protocol: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "protocol": self.protocol,
        }


@dataclass(slots=True)
class VPNAccess:
    id: int
    user_id: int
    server_name: str
    protocol: str
    credential_id: str
    access_url: str
    config_blob: str
    issued_at: datetime = field(default_factory=utc_now)
    expires_at: datetime = field(default_factory=utc_now)
    revoked_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None and self.expires_at > utc_now()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "server_name": self.server_name,
            "protocol": self.protocol,
            "credential_id": self.credential_id,
            "access_url": self.access_url,
            "config_blob": self.config_blob,
            "issued_at": isoformat(self.issued_at),
            "expires_at": isoformat(self.expires_at),
            "revoked_at": isoformat(self.revoked_at),
            "is_active": self.is_active,
        }
