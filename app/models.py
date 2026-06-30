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
    channel_gate_required: Mapped[bool] = mapped_column(Boolean, default=False)
    channel_gate_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    buy_clicks: Mapped[int] = mapped_column(Integer, default=0)
    support_clicks: Mapped[int] = mapped_column(Integer, default=0)
    replace_key_clicks: Mapped[int] = mapped_column(Integer, default=0)
    replace_key_successes: Mapped[int] = mapped_column(Integer, default=0)
    last_buy_clicked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_support_clicked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_replace_key_clicked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_replace_key_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    subscriptions: Mapped[list["Subscription"]] = relationship(back_populates="user")
    orders: Mapped[list["Order"]] = relationship(back_populates="user")
    keys: Mapped[list["VpnKey"]] = relationship(back_populates="user")


class Referral(Base):
    __tablename__ = "referrals"
    __table_args__ = (UniqueConstraint("referred_user_id", name="uq_referrals_referred_user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    referrer_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    referred_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    reward_days: Mapped[int] = mapped_column(Integer, default=3)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    credited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


class Plan(Base):
    __tablename__ = "plans"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    price_rub: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    days: Mapped[int] = mapped_column(Integer)
    traffic_gb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class VpnNode(Base):
    __tablename__ = "vpn_nodes"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    mode: Mapped[str] = mapped_column(String(32), default="mock")
    base_url: Mapped[str] = mapped_column(String(512))
    username: Mapped[str] = mapped_column(String(255))
    password: Mapped[str] = mapped_column(String(255))
    inbound_id: Mapped[int] = mapped_column(Integer, default=1)
    max_clients: Mapped[int] = mapped_column(Integer, default=10)
    public_host: Mapped[str] = mapped_column(String(255))
    public_port: Mapped[int] = mapped_column(Integer, default=8443)
    vless_query: Mapped[str] = mapped_column(String(512), default="type=tcp&security=none")
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="unknown", index=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    remote_clients: Mapped[int] = mapped_column(Integer, default=0)
    remote_enabled_clients: Mapped[int] = mapped_column(Integer, default=0)
    traffic_up_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    traffic_down_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    cpu_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    memory_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disk_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    keys: Mapped[list["VpnKey"]] = relationship(back_populates="node")


class DirectNode(Base):
    __tablename__ = "direct_nodes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    country_code: Mapped[str] = mapped_column(String(8), index=True)
    country_flag: Mapped[str] = mapped_column(String(16))
    public_host: Mapped[str] = mapped_column(String(255))
    public_ip: Mapped[str] = mapped_column(String(64))
    api_base_url: Mapped[str] = mapped_column(String(512))
    api_username: Mapped[str] = mapped_column(String(255), default="")
    api_password: Mapped[str] = mapped_column(String(255), default="")
    api_token: Mapped[str] = mapped_column(String(512), default="")
    api_verify_tls: Mapped[bool] = mapped_column(Boolean, default=False)
    api_timeout_seconds: Mapped[int] = mapped_column(Integer, default=20)
    agent_url: Mapped[str] = mapped_column(String(512))
    agent_token: Mapped[str] = mapped_column(String(255), default="")
    agent_verify_tls: Mapped[bool] = mapped_column(Boolean, default=False)
    agent_timeout_seconds: Mapped[int] = mapped_column(Integer, default=20)
    vpn_port_min: Mapped[int] = mapped_column(Integer, default=30000)
    vpn_port_max: Mapped[int] = mapped_column(Integer, default=39999)
    reserved_ports: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="unknown", index=True)
    last_health_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DirectInboundSlot(Base):
    __tablename__ = "direct_inbound_slots"
    __table_args__ = (
        UniqueConstraint("node_id", "port", name="uq_direct_inbound_slots_node_port"),
        UniqueConstraint("node_id", "slot_number", name="uq_direct_inbound_slots_node_slot_number"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("direct_nodes.id"), index=True)
    slot_number: Mapped[int] = mapped_column(Integer)
    template_code: Mapped[str] = mapped_column(String(128), default="", index=True)
    protocol: Mapped[str] = mapped_column(String(64), index=True)
    inbound_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    port: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(255))
    speed_limit_mbit: Mapped[int] = mapped_column(Integer, default=40)
    status: Mapped[str] = mapped_column(String(32), default="free", index=True)
    assigned_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    last_assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DirectUserProfile(Base):
    __tablename__ = "direct_user_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("direct_nodes.id"), index=True)
    inbound_slot_id: Mapped[int] = mapped_column(ForeignKey("direct_inbound_slots.id"), index=True)
    key_type: Mapped[str] = mapped_column(String(32), default="private", index=True)
    template_code: Mapped[str] = mapped_column(String(128), default="", index=True)
    protocol: Mapped[str] = mapped_column(String(64), index=True)
    inbound_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    port: Mapped[int] = mapped_column(Integer)
    client_id: Mapped[str] = mapped_column(String(128))
    uuid_or_password: Mapped[str] = mapped_column(String(255))
    public_link: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DirectSubscription(Base):
    __tablename__ = "direct_subscriptions"
    __table_args__ = (UniqueConstraint("subscription_token", name="uq_direct_subscriptions_token"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("direct_nodes.id"), index=True)
    key_type: Mapped[str] = mapped_column(String(32), default="private", index=True)
    subscription_token: Mapped[str] = mapped_column(String(128), index=True)
    subscription_url: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    plan_code: Mapped[str] = mapped_column(ForeignKey("plans.code"))
    payment_code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    amount_rub: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    payment_provider: Mapped[str] = mapped_column(String(32), default="donationalerts", index=True)
    moderation_status: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    moderation_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    manual_qr_file_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provisional_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    donation_alert_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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


class SubscriptionReminder(Base):
    __tablename__ = "subscription_reminders"
    __table_args__ = (UniqueConstraint("subscription_id", "reminder_type", name="uq_subscription_reminders_type"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("subscriptions.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    reminder_type: Mapped[str] = mapped_column(String(64), index=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    response: Mapped[str | None] = mapped_column(String(64), nullable=True)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserFeedback(Base):
    __tablename__ = "user_feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    subscription_id: Mapped[int | None] = mapped_column(ForeignKey("subscriptions.id"), nullable=True, index=True)
    reminder_id: Mapped[int | None] = mapped_column(ForeignKey("subscription_reminders.id"), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(64), default="trial_feedback", index=True)
    rating: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    sent_to_admin_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    user: Mapped[User] = relationship()
    subscription: Mapped[Subscription | None] = relationship()
    reminder: Mapped[SubscriptionReminder | None] = relationship()


class VpnKey(Base):
    __tablename__ = "vpn_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    node_id: Mapped[int | None] = mapped_column(ForeignKey("vpn_nodes.id"), nullable=True, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    subscription_id: Mapped[int | None] = mapped_column(ForeignKey("subscriptions.id"), nullable=True, index=True)
    key_type: Mapped[str] = mapped_column(String(32), default="private", index=True)
    x3ui_client_uuid: Mapped[str] = mapped_column(String(64), unique=True)
    x3ui_sub_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    x3ui_inbound_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    server_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    limit_ip: Mapped[int | None] = mapped_column(Integer, nullable=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    vless_uri: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User | None] = relationship(back_populates="keys")
    subscription: Mapped[Subscription | None] = relationship(back_populates="keys")
    node: Mapped[VpnNode | None] = relationship(back_populates="keys")


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


class UserKeyActivitySnapshot(Base):
    __tablename__ = "user_key_activity_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    key_id: Mapped[int | None] = mapped_column(ForeignKey("vpn_keys.id"), nullable=True, index=True)
    node_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    sampled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    online: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    up_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    down_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    inbound_count: Mapped[int] = mapped_column(Integer, default=0)
    active_inbounds: Mapped[int] = mapped_column(Integer, default=0)
    matched_clients: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(32), default="detail")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class BotAdmin(Base):
    __tablename__ = "bot_admins"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PublicKeyPostTemplate(Base):
    __tablename__ = "public_key_post_templates"
    __table_args__ = (UniqueConstraint("code", name="uq_public_key_post_templates_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Setting(Base):
    __tablename__ = "settings"
    __table_args__ = (UniqueConstraint("key", name="uq_settings_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(128), index=True)
    value: Mapped[str] = mapped_column(Text)
