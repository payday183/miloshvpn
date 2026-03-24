"""Add foundation schema for webapp, billing, and Marzban

Revision ID: 20260321_0001
Revises:
Create Date: 2026-03-21 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260321_0001"
down_revision = None
branch_labels = None
depends_on = None


CURRENT_TIMESTAMP = sa.text("CURRENT_TIMESTAMP")


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _table_exists(table_name: str) -> bool:
    return table_name in _inspector().get_table_names()


def _column_exists(table_name: str, column_name: str) -> bool:
    if not _table_exists(table_name):
        return False
    return any(column["name"] == column_name for column in _inspector().get_columns(table_name))


def _index_exists(table_name: str, index_name: str) -> bool:
    if not _table_exists(table_name):
        return False
    return any(index["name"] == index_name for index in _inspector().get_indexes(table_name))


def _foreign_key_exists(table_name: str, fk_name: str) -> bool:
    if not _table_exists(table_name):
        return False
    return any(fk["name"] == fk_name for fk in _inspector().get_foreign_keys(table_name))


def _create_table_if_missing(table_name: str, *columns: sa.Column, **kwargs: object) -> None:
    if _table_exists(table_name):
        return
    op.create_table(table_name, *columns, **kwargs)


def _ensure_index(table_name: str, index_name: str, columns: list[str], unique: bool = False) -> None:
    if (
        not _table_exists(table_name)
        or _index_exists(table_name, index_name)
        or any(not _column_exists(table_name, column_name) for column_name in columns)
    ):
        return
    op.create_index(index_name, table_name, columns, unique=unique)


def _ensure_column(table_name: str, column: sa.Column) -> None:
    if not _table_exists(table_name) or _column_exists(table_name, column.name):
        return
    op.add_column(table_name, column)


def _ensure_foreign_key(
    fk_name: str,
    source_table: str,
    referent_table: str,
    local_columns: list[str],
    remote_columns: list[str],
) -> None:
    if (
        not _table_exists(source_table)
        or not _table_exists(referent_table)
        or _foreign_key_exists(source_table, fk_name)
    ):
        return
    op.create_foreign_key(fk_name, source_table, referent_table, local_columns, remote_columns)


def _drop_index_if_exists(table_name: str, index_name: str) -> None:
    if _index_exists(table_name, index_name):
        op.drop_index(index_name, table_name=table_name)


def _drop_column_if_exists(table_name: str, column_name: str) -> None:
    if _column_exists(table_name, column_name):
        op.drop_column(table_name, column_name)


def _drop_foreign_key_if_exists(table_name: str, fk_name: str) -> None:
    if _foreign_key_exists(table_name, fk_name):
        op.drop_constraint(fk_name, table_name, type_="foreignkey")


def _drop_table_if_exists(table_name: str) -> None:
    if _table_exists(table_name):
        op.drop_table(table_name)


def _insert_if_missing(table_name: str, where_clause: str, values: dict[str, object]) -> None:
    bind = op.get_bind()
    exists = bind.execute(
        sa.text(f"SELECT 1 FROM {table_name} WHERE {where_clause} LIMIT 1"),
        values,
    ).scalar()
    if exists is not None:
        return

    columns = ", ".join(values.keys())
    placeholders = ", ".join(f":{key}" for key in values)
    bind.execute(
        sa.text(f"INSERT INTO {table_name} ({columns}) VALUES ({placeholders})"),
        values,
    )


def _create_base_tables() -> None:
    _create_table_if_missing(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("username", sa.String(), nullable=True),
        sa.Column("role", sa.String(), nullable=True, server_default="user"),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=CURRENT_TIMESTAMP),
    )
    _ensure_index("users", "ix_users_telegram_id", ["telegram_id"], unique=True)

    _create_table_if_missing(
        "payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("amount", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(), nullable=True, server_default="RUB"),
        sa.Column("status", sa.String(), nullable=True, server_default="pending"),
        sa.Column("provider", sa.String(), nullable=True, server_default="manual"),
        sa.Column("external_id", sa.String(), nullable=True),
        sa.Column("provider_payment_id", sa.String(), nullable=True),
        sa.Column("provider_status", sa.String(), nullable=True),
        sa.Column("plan", sa.String(), nullable=True, server_default="month"),
        sa.Column("duration_days", sa.Integer(), nullable=True),
        sa.Column("payment_code", sa.String(), nullable=True),
        sa.Column("recipient_phone", sa.String(), nullable=True),
        sa.Column("payment_method", sa.String(), nullable=True),
        sa.Column("confirmation_url", sa.Text(), nullable=True),
        sa.Column("idempotence_key", sa.String(), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
        sa.Column("canceled_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=CURRENT_TIMESTAMP),
    )
    _ensure_index("payments", "ix_payments_payment_code", ["payment_code"], unique=True)
    _ensure_index("payments", "ix_payments_provider_payment_id", ["provider_payment_id"], unique=True)

    _create_table_if_missing(
        "subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("plan", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("ends_at", sa.DateTime(), nullable=True),
        sa.Column("source_payment_id", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True, server_default=CURRENT_TIMESTAMP),
    )

    _create_table_if_missing(
        "vpn_access",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("server_id", sa.Integer(), nullable=True),
        sa.Column("server_name", sa.String(), nullable=True),
        sa.Column("protocol", sa.String(), nullable=True),
        sa.Column("credential_id", sa.String(), nullable=True),
        sa.Column("external_user_id", sa.String(), nullable=True),
        sa.Column("access_url", sa.String(), nullable=True),
        sa.Column("subscription_url", sa.Text(), nullable=True),
        sa.Column("config_blob", sa.String(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("sync_status", sa.String(), nullable=True, server_default="pending"),
        sa.Column("usage_bytes", sa.BigInteger(), nullable=True, server_default="0"),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("provisioning_error", sa.Text(), nullable=True),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def _create_feature_tables() -> None:
    _create_table_if_missing(
        "plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=CURRENT_TIMESTAMP),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=CURRENT_TIMESTAMP),
    )
    _ensure_index("plans", "ix_plans_code", ["code"], unique=True)

    _create_table_if_missing(
        "plan_periods",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("period_key", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("period_months", sa.Integer(), nullable=False),
        sa.Column("duration_days", sa.Integer(), nullable=False),
        sa.Column("price_amount", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False, server_default="RUB"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=CURRENT_TIMESTAMP),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=CURRENT_TIMESTAMP),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], name="fk_plan_periods_plan_id"),
    )
    _ensure_index("plan_periods", "ix_plan_periods_plan_id", ["plan_id"])
    _ensure_index("plan_periods", "ix_plan_periods_period_key", ["period_key"])

    _create_table_if_missing(
        "content_blocks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("format", sa.String(), nullable=False, server_default="markdown"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_by_telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=CURRENT_TIMESTAMP),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=CURRENT_TIMESTAMP),
    )
    _ensure_index("content_blocks", "ix_content_blocks_key", ["key"], unique=True)

    _create_table_if_missing(
        "admin_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("is_secret", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_by_telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=CURRENT_TIMESTAMP),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=CURRENT_TIMESTAMP),
    )
    _ensure_index("admin_settings", "ix_admin_settings_key", ["key"], unique=True)

    _create_table_if_missing(
        "webapp_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("session_token", sa.String(), nullable=False),
        sa.Column("scope", sa.String(), nullable=False, server_default="user"),
        sa.Column("init_data_hash", sa.String(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=CURRENT_TIMESTAMP),
    )
    _ensure_index("webapp_sessions", "ix_webapp_sessions_user_id", ["user_id"])
    _ensure_index("webapp_sessions", "ix_webapp_sessions_telegram_id", ["telegram_id"])
    _ensure_index("webapp_sessions", "ix_webapp_sessions_session_token", ["session_token"], unique=True)

    _create_table_if_missing(
        "payment_webhook_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(), nullable=False, server_default="yookassa"),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("provider_object_id", sa.String(), nullable=True),
        sa.Column("payment_id", sa.Integer(), nullable=True),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("is_processed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=CURRENT_TIMESTAMP),
    )
    _ensure_index("payment_webhook_events", "ix_payment_webhook_events_event_type", ["event_type"])
    _ensure_index(
        "payment_webhook_events",
        "ix_payment_webhook_events_provider_object_id",
        ["provider_object_id"],
    )
    _ensure_index("payment_webhook_events", "ix_payment_webhook_events_payment_id", ["payment_id"])

    _create_table_if_missing(
        "marzban_servers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("location", sa.String(), nullable=True),
        sa.Column("api_base_url", sa.String(), nullable=False),
        sa.Column("dashboard_url", sa.String(), nullable=True),
        sa.Column("username", sa.String(), nullable=True),
        sa.Column("password", sa.String(), nullable=True),
        sa.Column("access_token", sa.Text(), nullable=True),
        sa.Column("inbound_tags", sa.Text(), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("allow_new_users", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("weight", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("max_users", sa.Integer(), nullable=True),
        sa.Column("max_traffic_bytes", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="unknown"),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=CURRENT_TIMESTAMP),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=CURRENT_TIMESTAMP),
    )
    _ensure_index("marzban_servers", "ix_marzban_servers_name", ["name"], unique=True)

    _create_table_if_missing(
        "marzban_server_metrics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("active_users", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active_subscriptions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_traffic_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("traffic_24h_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cpu_percent", sa.Float(), nullable=True),
        sa.Column("memory_percent", sa.Float(), nullable=True),
        sa.Column("network_rx_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("network_tx_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("load_score", sa.Float(), nullable=True),
        sa.Column("is_overloaded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("collected_at", sa.DateTime(), nullable=False, server_default=CURRENT_TIMESTAMP),
        sa.ForeignKeyConstraint(["server_id"], ["marzban_servers.id"], name="fk_marzban_server_metrics_server_id"),
    )
    _ensure_index("marzban_server_metrics", "ix_marzban_server_metrics_server_id", ["server_id"], unique=True)

    _create_table_if_missing(
        "marzban_server_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("active_users", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_traffic_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("traffic_24h_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cpu_percent", sa.Float(), nullable=True),
        sa.Column("memory_percent", sa.Float(), nullable=True),
        sa.Column("load_score", sa.Float(), nullable=True),
        sa.Column("is_overloaded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("collected_at", sa.DateTime(), nullable=False, server_default=CURRENT_TIMESTAMP),
        sa.ForeignKeyConstraint(["server_id"], ["marzban_servers.id"], name="fk_marzban_server_snapshots_server_id"),
    )
    _ensure_index("marzban_server_snapshots", "ix_marzban_server_snapshots_server_id", ["server_id"])
    _ensure_index("marzban_server_snapshots", "ix_marzban_server_snapshots_collected_at", ["collected_at"])

    _create_table_if_missing(
        "user_activity_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("payload", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=CURRENT_TIMESTAMP),
    )
    _ensure_index("user_activity_events", "ix_user_activity_events_user_id", ["user_id"])
    _ensure_index("user_activity_events", "ix_user_activity_events_telegram_id", ["telegram_id"])
    _ensure_index("user_activity_events", "ix_user_activity_events_event_type", ["event_type"])
    _ensure_index("user_activity_events", "ix_user_activity_events_created_at", ["created_at"])

    _create_table_if_missing(
        "user_server_assignments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("decision_payload", sa.Text(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("assigned_at", sa.DateTime(), nullable=False, server_default=CURRENT_TIMESTAMP),
        sa.Column("released_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["server_id"], ["marzban_servers.id"], name="fk_user_server_assignments_server_id"),
    )
    _ensure_index("user_server_assignments", "ix_user_server_assignments_user_id", ["user_id"])
    _ensure_index("user_server_assignments", "ix_user_server_assignments_server_id", ["server_id"])

    _create_table_if_missing(
        "user_usage_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=True),
        sa.Column("upload_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("download_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("captured_at", sa.DateTime(), nullable=False, server_default=CURRENT_TIMESTAMP),
        sa.ForeignKeyConstraint(["server_id"], ["marzban_servers.id"], name="fk_user_usage_snapshots_server_id"),
    )
    _ensure_index("user_usage_snapshots", "ix_user_usage_snapshots_user_id", ["user_id"])
    _ensure_index("user_usage_snapshots", "ix_user_usage_snapshots_server_id", ["server_id"])
    _ensure_index("user_usage_snapshots", "ix_user_usage_snapshots_captured_at", ["captured_at"])


def _upgrade_existing_legacy_tables() -> None:
    _ensure_column("payments", sa.Column("provider_payment_id", sa.String(), nullable=True))
    _ensure_column("payments", sa.Column("provider_status", sa.String(), nullable=True))
    _ensure_column("payments", sa.Column("payment_method", sa.String(), nullable=True))
    _ensure_column("payments", sa.Column("confirmation_url", sa.Text(), nullable=True))
    _ensure_column("payments", sa.Column("idempotence_key", sa.String(), nullable=True))
    _ensure_column("payments", sa.Column("metadata_json", sa.Text(), nullable=True))
    _ensure_column("payments", sa.Column("error_message", sa.Text(), nullable=True))
    _ensure_column("payments", sa.Column("paid_at", sa.DateTime(), nullable=True))
    _ensure_column("payments", sa.Column("canceled_at", sa.DateTime(), nullable=True))
    _ensure_column("payments", sa.Column("updated_at", sa.DateTime(), nullable=True))
    _ensure_index("payments", "ix_payments_provider_payment_id", ["provider_payment_id"], unique=True)

    _ensure_column("subscriptions", sa.Column("source_payment_id", sa.Integer(), nullable=True))
    _ensure_column("subscriptions", sa.Column("updated_at", sa.DateTime(), nullable=True))
    _ensure_column(
        "subscriptions",
        sa.Column("created_at", sa.DateTime(), nullable=True, server_default=CURRENT_TIMESTAMP),
    )

    _ensure_column("vpn_access", sa.Column("server_id", sa.Integer(), nullable=True))
    _ensure_column("vpn_access", sa.Column("external_user_id", sa.String(), nullable=True))
    _ensure_column("vpn_access", sa.Column("subscription_url", sa.Text(), nullable=True))
    _ensure_column("vpn_access", sa.Column("last_synced_at", sa.DateTime(), nullable=True))
    _ensure_column("vpn_access", sa.Column("sync_status", sa.String(), nullable=True))
    _ensure_column("vpn_access", sa.Column("usage_bytes", sa.BigInteger(), nullable=True))
    _ensure_column("vpn_access", sa.Column("last_used_at", sa.DateTime(), nullable=True))
    _ensure_column("vpn_access", sa.Column("provisioning_error", sa.Text(), nullable=True))
    _ensure_foreign_key(
        "fk_vpn_access_server_id",
        "vpn_access",
        "marzban_servers",
        ["server_id"],
        ["id"],
    )


def _seed_defaults() -> None:
    bind = op.get_bind()

    plan_id = bind.execute(
        sa.text("SELECT id FROM plans WHERE code = :code LIMIT 1"),
        {"code": "vpn"},
    ).scalar()
    if plan_id is None:
        plan_id = bind.execute(
            sa.text(
                """
                INSERT INTO plans (code, name, description, is_active, sort_order, created_at, updated_at)
                VALUES (:code, :name, :description, :is_active, :sort_order, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                RETURNING id
                """
            ),
            {
                "code": "vpn",
                "name": "VPN",
                "description": "Default VPN subscription plan",
                "is_active": True,
                "sort_order": 10,
            },
        ).scalar_one()

    for period in [
        {
            "plan_id": plan_id,
            "period_key": "1_month",
            "label": "1 month",
            "period_months": 1,
            "duration_days": 30,
            "price_amount": 300.0,
            "currency": "RUB",
            "is_active": True,
            "sort_order": 10,
        },
        {
            "plan_id": plan_id,
            "period_key": "3_month",
            "label": "3 months",
            "period_months": 3,
            "duration_days": 90,
            "price_amount": 800.0,
            "currency": "RUB",
            "is_active": True,
            "sort_order": 20,
        },
        {
            "plan_id": plan_id,
            "period_key": "6_month",
            "label": "6 months",
            "period_months": 6,
            "duration_days": 180,
            "price_amount": 1500.0,
            "currency": "RUB",
            "is_active": True,
            "sort_order": 30,
        },
        {
            "plan_id": plan_id,
            "period_key": "12_month",
            "label": "12 months",
            "period_months": 12,
            "duration_days": 365,
            "price_amount": 2800.0,
            "currency": "RUB",
            "is_active": True,
            "sort_order": 40,
        },
    ]:
        _insert_if_missing(
            "plan_periods",
            "plan_id = :plan_id AND period_key = :period_key",
            period,
        )

    for block in [
        {
            "key": "purchase_intro",
            "title": "Purchase intro",
            "body": "Choose a plan and complete payment.",
            "format": "markdown",
            "is_active": True,
        },
        {
            "key": "post_purchase_intro",
            "title": "Post purchase intro",
            "body": "Payment completed. Use your access key below.",
            "format": "markdown",
            "is_active": True,
        },
        {
            "key": "connection_instructions",
            "title": "Connection instructions",
            "body": "Install a client app, paste the key, and connect.",
            "format": "markdown",
            "is_active": True,
        },
    ]:
        _insert_if_missing("content_blocks", "key = :key", block)

    for setting in [
        {"key": "yookassa_shop_id", "value": None, "is_secret": False},
        {"key": "yookassa_secret_key", "value": None, "is_secret": True},
        {"key": "yookassa_mode", "value": "test", "is_secret": False},
    ]:
        _insert_if_missing("admin_settings", "key = :key", setting)


def upgrade() -> None:
    _create_base_tables()
    _create_feature_tables()
    _upgrade_existing_legacy_tables()
    _seed_defaults()


def downgrade() -> None:
    _drop_foreign_key_if_exists("vpn_access", "fk_vpn_access_server_id")
    _drop_column_if_exists("vpn_access", "provisioning_error")
    _drop_column_if_exists("vpn_access", "last_used_at")
    _drop_column_if_exists("vpn_access", "usage_bytes")
    _drop_column_if_exists("vpn_access", "sync_status")
    _drop_column_if_exists("vpn_access", "last_synced_at")
    _drop_column_if_exists("vpn_access", "subscription_url")
    _drop_column_if_exists("vpn_access", "external_user_id")
    _drop_column_if_exists("vpn_access", "server_id")

    _drop_column_if_exists("subscriptions", "created_at")
    _drop_column_if_exists("subscriptions", "updated_at")
    _drop_column_if_exists("subscriptions", "source_payment_id")

    _drop_index_if_exists("payments", "ix_payments_provider_payment_id")
    _drop_column_if_exists("payments", "updated_at")
    _drop_column_if_exists("payments", "canceled_at")
    _drop_column_if_exists("payments", "paid_at")
    _drop_column_if_exists("payments", "error_message")
    _drop_column_if_exists("payments", "metadata_json")
    _drop_column_if_exists("payments", "idempotence_key")
    _drop_column_if_exists("payments", "confirmation_url")
    _drop_column_if_exists("payments", "payment_method")
    _drop_column_if_exists("payments", "provider_status")
    _drop_column_if_exists("payments", "provider_payment_id")

    _drop_index_if_exists("user_usage_snapshots", "ix_user_usage_snapshots_captured_at")
    _drop_index_if_exists("user_usage_snapshots", "ix_user_usage_snapshots_server_id")
    _drop_index_if_exists("user_usage_snapshots", "ix_user_usage_snapshots_user_id")
    _drop_table_if_exists("user_usage_snapshots")

    _drop_index_if_exists("user_server_assignments", "ix_user_server_assignments_server_id")
    _drop_index_if_exists("user_server_assignments", "ix_user_server_assignments_user_id")
    _drop_table_if_exists("user_server_assignments")

    _drop_index_if_exists("user_activity_events", "ix_user_activity_events_created_at")
    _drop_index_if_exists("user_activity_events", "ix_user_activity_events_event_type")
    _drop_index_if_exists("user_activity_events", "ix_user_activity_events_telegram_id")
    _drop_index_if_exists("user_activity_events", "ix_user_activity_events_user_id")
    _drop_table_if_exists("user_activity_events")

    _drop_index_if_exists("marzban_server_snapshots", "ix_marzban_server_snapshots_collected_at")
    _drop_index_if_exists("marzban_server_snapshots", "ix_marzban_server_snapshots_server_id")
    _drop_table_if_exists("marzban_server_snapshots")

    _drop_index_if_exists("marzban_server_metrics", "ix_marzban_server_metrics_server_id")
    _drop_table_if_exists("marzban_server_metrics")

    _drop_index_if_exists("marzban_servers", "ix_marzban_servers_name")
    _drop_table_if_exists("marzban_servers")

    _drop_index_if_exists("payment_webhook_events", "ix_payment_webhook_events_payment_id")
    _drop_index_if_exists("payment_webhook_events", "ix_payment_webhook_events_provider_object_id")
    _drop_index_if_exists("payment_webhook_events", "ix_payment_webhook_events_event_type")
    _drop_table_if_exists("payment_webhook_events")

    _drop_index_if_exists("webapp_sessions", "ix_webapp_sessions_session_token")
    _drop_index_if_exists("webapp_sessions", "ix_webapp_sessions_telegram_id")
    _drop_index_if_exists("webapp_sessions", "ix_webapp_sessions_user_id")
    _drop_table_if_exists("webapp_sessions")

    _drop_index_if_exists("admin_settings", "ix_admin_settings_key")
    _drop_table_if_exists("admin_settings")

    _drop_index_if_exists("content_blocks", "ix_content_blocks_key")
    _drop_table_if_exists("content_blocks")

    _drop_index_if_exists("plan_periods", "ix_plan_periods_period_key")
    _drop_index_if_exists("plan_periods", "ix_plan_periods_plan_id")
    _drop_table_if_exists("plan_periods")

    _drop_index_if_exists("plans", "ix_plans_code")
    _drop_table_if_exists("plans")
