from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, JSON, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(32), default="user", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    subscriptions: Mapped[list["Subscription"]] = relationship(back_populates="user")
    orders: Mapped[list["Order"]] = relationship(back_populates="user")
    keys: Mapped[list["VpnKey"]] = relationship(back_populates="user")


class Plan(Base):
    __tablename__ = "plans"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    price_rub: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    days: Mapped[int] = mapped_column(Integer)
    traffic_gb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    plan_code: Mapped[str] = mapped_column(ForeignKey("plans.code"))
    payment_code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    amount_rub: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    donation_alert_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="orders")
    plan: Mapped[Plan] = relationship()


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    plan_code: Mapped[str] = mapped_column(ForeignKey("plans.code"))
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    traffic_limit_gb: Mapped[int | None] = mapped_column(Integer, nullable=True)

    user: Mapped[User] = relationship(back_populates="subscriptions")
    plan: Mapped[Plan] = relationship()
    keys: Mapped[list["VpnKey"]] = relationship(back_populates="subscription")


class VpnKey(Base):
    __tablename__ = "vpn_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    subscription_id: Mapped[int | None] = mapped_column(ForeignKey("subscriptions.id"), nullable=True, index=True)
    key_type: Mapped[str] = mapped_column(String(32), default="private", index=True)
    x3ui_client_uuid: Mapped[str] = mapped_column(String(64), unique=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    vless_uri: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User | None] = relationship(back_populates="keys")
    subscription: Mapped[Subscription | None] = relationship(back_populates="keys")


class DonationEvent(Base):
    __tablename__ = "donation_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict[str, Any]] = mapped_column(JSON)


class BotAdmin(Base):
    __tablename__ = "bot_admins"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Setting(Base):
    __tablename__ = "settings"
    __table_args__ = (UniqueConstraint("key", name="uq_settings_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(128), index=True)
    value: Mapped[str] = mapped_column(Text)
