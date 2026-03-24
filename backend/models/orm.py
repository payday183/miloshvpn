from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
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
    provider_payment_id = Column(String, unique=True, index=True, nullable=True)
    provider_status = Column(String, nullable=True)
    plan = Column(String, default="month")
    duration_days = Column(Integer, nullable=True)
    payment_code = Column(String, unique=True, index=True, nullable=True)
    recipient_phone = Column(String, nullable=True)
    payment_method = Column(String, nullable=True)
    confirmation_url = Column(Text, nullable=True)
    idempotence_key = Column(String, nullable=True)
    metadata_json = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    paid_at = Column(DateTime, nullable=True)
    canceled_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)


class SubscriptionORM(Base):
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    plan = Column(String)
    status = Column(String)
    started_at = Column(DateTime)
    ends_at = Column(DateTime)
    source_payment_id = Column(Integer, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)


class VPNAccessORM(Base):
    __tablename__ = "vpn_access"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    server_id = Column(Integer, ForeignKey("marzban_servers.id"), nullable=True)
    server_name = Column(String)
    protocol = Column(String)
    credential_id = Column(String)
    external_user_id = Column(String, nullable=True)
    access_url = Column(String)
    subscription_url = Column(Text, nullable=True)
    config_blob = Column(String)
    expires_at = Column(DateTime)
    last_synced_at = Column(DateTime, nullable=True)
    sync_status = Column(String, default="pending")
    usage_bytes = Column(BigInteger, default=0)
    last_used_at = Column(DateTime, nullable=True)
    provisioning_error = Column(Text, nullable=True)
    revoked = Column(Boolean, default=False)


class PlanORM(Base):
    __tablename__ = "plans"

    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True, index=True)
    name = Column(String)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PlanPeriodORM(Base):
    __tablename__ = "plan_periods"

    id = Column(Integer, primary_key=True)
    plan_id = Column(Integer, ForeignKey("plans.id"), index=True)
    period_key = Column(String, index=True)
    label = Column(String)
    period_months = Column(Integer)
    duration_days = Column(Integer)
    price_amount = Column(Float)
    currency = Column(String, default="RUB")
    is_active = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ContentBlockORM(Base):
    __tablename__ = "content_blocks"

    id = Column(Integer, primary_key=True)
    key = Column(String, unique=True, index=True)
    title = Column(String, nullable=True)
    body = Column(Text)
    format = Column(String, default="markdown")
    is_active = Column(Boolean, default=True)
    updated_by_telegram_id = Column(BigInteger, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AdminSettingORM(Base):
    __tablename__ = "admin_settings"

    id = Column(Integer, primary_key=True)
    key = Column(String, unique=True, index=True)
    value = Column(Text, nullable=True)
    is_secret = Column(Boolean, default=False)
    updated_by_telegram_id = Column(BigInteger, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class WebAppSessionORM(Base):
    __tablename__ = "webapp_sessions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=True, index=True)
    telegram_id = Column(BigInteger, index=True)
    session_token = Column(String, unique=True, index=True)
    scope = Column(String, default="user")
    init_data_hash = Column(String, nullable=True)
    expires_at = Column(DateTime)
    last_seen_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class PaymentWebhookEventORM(Base):
    __tablename__ = "payment_webhook_events"

    id = Column(Integer, primary_key=True)
    provider = Column(String, default="yookassa")
    event_type = Column(String, index=True)
    provider_object_id = Column(String, index=True, nullable=True)
    payment_id = Column(Integer, nullable=True, index=True)
    payload = Column(Text)
    is_processed = Column(Boolean, default=False)
    processed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class MarzbanServerORM(Base):
    __tablename__ = "marzban_servers"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, index=True)
    location = Column(String, nullable=True)
    api_base_url = Column(String)
    dashboard_url = Column(String, nullable=True)
    username = Column(String, nullable=True)
    password = Column(String, nullable=True)
    access_token = Column(Text, nullable=True)
    inbound_tags = Column(Text, nullable=True)
    is_enabled = Column(Boolean, default=True)
    allow_new_users = Column(Boolean, default=True)
    weight = Column(Integer, default=100)
    max_users = Column(Integer, nullable=True)
    max_traffic_bytes = Column(BigInteger, nullable=True)
    status = Column(String, default="unknown")
    last_checked_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MarzbanServerMetricORM(Base):
    __tablename__ = "marzban_server_metrics"

    id = Column(Integer, primary_key=True)
    server_id = Column(Integer, ForeignKey("marzban_servers.id"), unique=True, index=True)
    active_users = Column(Integer, default=0)
    active_subscriptions = Column(Integer, default=0)
    total_traffic_bytes = Column(BigInteger, default=0)
    traffic_24h_bytes = Column(BigInteger, default=0)
    cpu_percent = Column(Float, nullable=True)
    memory_percent = Column(Float, nullable=True)
    network_rx_bytes = Column(BigInteger, default=0)
    network_tx_bytes = Column(BigInteger, default=0)
    load_score = Column(Float, nullable=True)
    is_overloaded = Column(Boolean, default=False)
    collected_at = Column(DateTime, default=datetime.utcnow)


class MarzbanServerSnapshotORM(Base):
    __tablename__ = "marzban_server_snapshots"

    id = Column(Integer, primary_key=True)
    server_id = Column(Integer, ForeignKey("marzban_servers.id"), index=True)
    active_users = Column(Integer, default=0)
    total_traffic_bytes = Column(BigInteger, default=0)
    traffic_24h_bytes = Column(BigInteger, default=0)
    cpu_percent = Column(Float, nullable=True)
    memory_percent = Column(Float, nullable=True)
    load_score = Column(Float, nullable=True)
    is_overloaded = Column(Boolean, default=False)
    collected_at = Column(DateTime, default=datetime.utcnow, index=True)


class UserActivityEventORM(Base):
    __tablename__ = "user_activity_events"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True)
    telegram_id = Column(BigInteger, nullable=True, index=True)
    event_type = Column(String, index=True)
    source = Column(String, nullable=True)
    payload = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class UserServerAssignmentORM(Base):
    __tablename__ = "user_server_assignments"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True)
    server_id = Column(Integer, ForeignKey("marzban_servers.id"), index=True)
    reason = Column(String, nullable=True)
    decision_payload = Column(Text, nullable=True)
    score = Column(Float, nullable=True)
    is_active = Column(Boolean, default=True)
    assigned_at = Column(DateTime, default=datetime.utcnow)
    released_at = Column(DateTime, nullable=True)


class UserUsageSnapshotORM(Base):
    __tablename__ = "user_usage_snapshots"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True)
    server_id = Column(Integer, ForeignKey("marzban_servers.id"), nullable=True, index=True)
    upload_bytes = Column(BigInteger, default=0)
    download_bytes = Column(BigInteger, default=0)
    total_bytes = Column(BigInteger, default=0)
    active = Column(Boolean, default=True)
    captured_at = Column(DateTime, default=datetime.utcnow, index=True)
