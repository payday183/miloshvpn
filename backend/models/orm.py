from sqlalchemy import Column, Integer, BigInteger, String, Float, DateTime, Boolean
from datetime import datetime

from shared.postgres import Base


class UserORM(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, index=True)
    username = Column(String)
    role = Column(String, default="user")
    phone = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class PaymentORM(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    amount = Column(Float)
    currency = Column(String, default="RUB")
    status = Column(String, default="pending")
    provider = Column(String, default="manual")
    external_id = Column(String, nullable=True)
    plan = Column(String, default="month")
    duration_days = Column(Integer, nullable=True)
    payment_code = Column(String, unique=True, index=True, nullable=True)
    recipient_phone = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class SubscriptionORM(Base):
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    plan = Column(String)
    status = Column(String)
    started_at = Column(DateTime)
    ends_at = Column(DateTime)


class VPNAccessORM(Base):
    __tablename__ = "vpn_access"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    server_name = Column(String)
    protocol = Column(String)
    credential_id = Column(String)
    access_url = Column(String)
    config_blob = Column(String)
    expires_at = Column(DateTime)
    revoked = Column(Boolean, default=False)
