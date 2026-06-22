from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def run_lightweight_migrations(conn: AsyncConnection) -> None:
    await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS channel_gate_required BOOLEAN DEFAULT FALSE"))
    await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS channel_gate_completed_at TIMESTAMP WITH TIME ZONE"))
    await conn.execute(text("UPDATE users SET channel_gate_required = FALSE WHERE channel_gate_required IS NULL"))
    await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS buy_clicks INTEGER DEFAULT 0"))
    await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS support_clicks INTEGER DEFAULT 0"))
    await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS replace_key_clicks INTEGER DEFAULT 0"))
    await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS replace_key_successes INTEGER DEFAULT 0"))
    await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_buy_clicked_at TIMESTAMP WITH TIME ZONE"))
    await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_support_clicked_at TIMESTAMP WITH TIME ZONE"))
    await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_replace_key_clicked_at TIMESTAMP WITH TIME ZONE"))
    await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_replace_key_completed_at TIMESTAMP WITH TIME ZONE"))
    await conn.execute(text("UPDATE users SET buy_clicks = 0 WHERE buy_clicks IS NULL"))
    await conn.execute(text("UPDATE users SET support_clicks = 0 WHERE support_clicks IS NULL"))
    await conn.execute(text("UPDATE users SET replace_key_clicks = 0 WHERE replace_key_clicks IS NULL"))
    await conn.execute(text("UPDATE users SET replace_key_successes = 0 WHERE replace_key_successes IS NULL"))
    await conn.execute(
        text(
            "CREATE TABLE IF NOT EXISTS referrals ("
            "id SERIAL PRIMARY KEY, "
            "referrer_user_id INTEGER NOT NULL REFERENCES users(id), "
            "referred_user_id INTEGER NOT NULL REFERENCES users(id), "
            "reward_days INTEGER NOT NULL DEFAULT 3, "
            "created_at TIMESTAMP WITH TIME ZONE NOT NULL, "
            "credited_at TIMESTAMP WITH TIME ZONE, "
            "CONSTRAINT uq_referrals_referred_user_id UNIQUE (referred_user_id)"
            ")"
        )
    )
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_referrals_referrer_user_id ON referrals (referrer_user_id)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_referrals_credited_at ON referrals (credited_at)"))
    await conn.execute(
        text(
            "CREATE TABLE IF NOT EXISTS direct_nodes ("
            "id VARCHAR(64) PRIMARY KEY, "
            "name VARCHAR(255) NOT NULL, "
            "country_code VARCHAR(8) NOT NULL, "
            "country_flag VARCHAR(16) NOT NULL, "
            "public_host VARCHAR(255) NOT NULL, "
            "public_ip VARCHAR(64) NOT NULL, "
            "api_base_url VARCHAR(512) NOT NULL, "
            "api_username VARCHAR(255) NOT NULL DEFAULT '', "
            "api_password VARCHAR(255) NOT NULL DEFAULT '', "
            "api_token VARCHAR(512) NOT NULL DEFAULT '', "
            "api_verify_tls BOOLEAN NOT NULL DEFAULT FALSE, "
            "api_timeout_seconds INTEGER NOT NULL DEFAULT 20, "
            "agent_url VARCHAR(512) NOT NULL, "
            "agent_token VARCHAR(255) NOT NULL DEFAULT '', "
            "agent_verify_tls BOOLEAN NOT NULL DEFAULT FALSE, "
            "agent_timeout_seconds INTEGER NOT NULL DEFAULT 20, "
            "vpn_port_min INTEGER NOT NULL DEFAULT 30000, "
            "vpn_port_max INTEGER NOT NULL DEFAULT 39999, "
            "reserved_ports TEXT NOT NULL DEFAULT '', "
            "status VARCHAR(32) DEFAULT 'unknown', "
            "last_health_at TIMESTAMP WITH TIME ZONE, "
            "created_at TIMESTAMP WITH TIME ZONE NOT NULL, "
            "updated_at TIMESTAMP WITH TIME ZONE NOT NULL"
            ")"
        )
    )
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_direct_nodes_country_code ON direct_nodes (country_code)"))
    await conn.execute(text("ALTER TABLE direct_nodes ADD COLUMN IF NOT EXISTS api_username VARCHAR(255) NOT NULL DEFAULT ''"))
    await conn.execute(text("ALTER TABLE direct_nodes ADD COLUMN IF NOT EXISTS api_password VARCHAR(255) NOT NULL DEFAULT ''"))
    await conn.execute(text("ALTER TABLE direct_nodes ADD COLUMN IF NOT EXISTS api_token VARCHAR(512) NOT NULL DEFAULT ''"))
    await conn.execute(text("ALTER TABLE direct_nodes ADD COLUMN IF NOT EXISTS api_verify_tls BOOLEAN NOT NULL DEFAULT FALSE"))
    await conn.execute(text("ALTER TABLE direct_nodes ADD COLUMN IF NOT EXISTS api_timeout_seconds INTEGER NOT NULL DEFAULT 20"))
    await conn.execute(text("ALTER TABLE direct_nodes ADD COLUMN IF NOT EXISTS agent_token VARCHAR(255) NOT NULL DEFAULT ''"))
    await conn.execute(text("ALTER TABLE direct_nodes ADD COLUMN IF NOT EXISTS agent_verify_tls BOOLEAN NOT NULL DEFAULT FALSE"))
    await conn.execute(text("ALTER TABLE direct_nodes ADD COLUMN IF NOT EXISTS agent_timeout_seconds INTEGER NOT NULL DEFAULT 20"))
    await conn.execute(text("ALTER TABLE direct_nodes ADD COLUMN IF NOT EXISTS vpn_port_min INTEGER NOT NULL DEFAULT 30000"))
    await conn.execute(text("ALTER TABLE direct_nodes ADD COLUMN IF NOT EXISTS vpn_port_max INTEGER NOT NULL DEFAULT 39999"))
    await conn.execute(text("ALTER TABLE direct_nodes ADD COLUMN IF NOT EXISTS reserved_ports TEXT NOT NULL DEFAULT ''"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_direct_nodes_status ON direct_nodes (status)"))
    await conn.execute(
        text(
            "CREATE TABLE IF NOT EXISTS direct_inbound_slots ("
            "id SERIAL PRIMARY KEY, "
            "node_id VARCHAR(64) NOT NULL REFERENCES direct_nodes(id), "
            "slot_number INTEGER NOT NULL, "
            "template_code VARCHAR(128) NOT NULL DEFAULT '', "
            "protocol VARCHAR(64) NOT NULL, "
            "inbound_id INTEGER, "
            "port INTEGER NOT NULL, "
            "name VARCHAR(255) NOT NULL, "
            "speed_limit_mbit INTEGER NOT NULL DEFAULT 40, "
            "status VARCHAR(32) DEFAULT 'free', "
            "assigned_user_id INTEGER REFERENCES users(id), "
            "last_assigned_at TIMESTAMP WITH TIME ZONE, "
            "created_at TIMESTAMP WITH TIME ZONE NOT NULL, "
            "updated_at TIMESTAMP WITH TIME ZONE NOT NULL, "
            "CONSTRAINT uq_direct_inbound_slots_node_port UNIQUE (node_id, port), "
            "CONSTRAINT uq_direct_inbound_slots_node_slot_number UNIQUE (node_id, slot_number)"
            ")"
        )
    )
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_direct_inbound_slots_node_id ON direct_inbound_slots (node_id)"))
    await conn.execute(text("ALTER TABLE direct_inbound_slots ADD COLUMN IF NOT EXISTS template_code VARCHAR(128) NOT NULL DEFAULT ''"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_direct_inbound_slots_template_code ON direct_inbound_slots (template_code)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_direct_inbound_slots_protocol ON direct_inbound_slots (protocol)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_direct_inbound_slots_status ON direct_inbound_slots (status)"))
    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_direct_inbound_slots_assigned_user_id ON direct_inbound_slots (assigned_user_id)")
    )
    await conn.execute(
        text(
            "CREATE TABLE IF NOT EXISTS direct_user_profiles ("
            "id SERIAL PRIMARY KEY, "
            "user_id INTEGER NOT NULL REFERENCES users(id), "
            "node_id VARCHAR(64) NOT NULL REFERENCES direct_nodes(id), "
            "inbound_slot_id INTEGER NOT NULL REFERENCES direct_inbound_slots(id), "
            "template_code VARCHAR(128) NOT NULL DEFAULT '', "
            "protocol VARCHAR(64) NOT NULL, "
            "inbound_id INTEGER, "
            "port INTEGER NOT NULL, "
            "client_id VARCHAR(128) NOT NULL, "
            "uuid_or_password VARCHAR(255) NOT NULL, "
            "public_link TEXT NOT NULL, "
            "status VARCHAR(32) DEFAULT 'active', "
            "created_at TIMESTAMP WITH TIME ZONE NOT NULL, "
            "updated_at TIMESTAMP WITH TIME ZONE NOT NULL"
            ")"
        )
    )
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_direct_user_profiles_user_id ON direct_user_profiles (user_id)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_direct_user_profiles_node_id ON direct_user_profiles (node_id)"))
    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_direct_user_profiles_inbound_slot_id ON direct_user_profiles (inbound_slot_id)")
    )
    await conn.execute(text("ALTER TABLE direct_user_profiles ADD COLUMN IF NOT EXISTS template_code VARCHAR(128) NOT NULL DEFAULT ''"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_direct_user_profiles_template_code ON direct_user_profiles (template_code)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_direct_user_profiles_protocol ON direct_user_profiles (protocol)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_direct_user_profiles_status ON direct_user_profiles (status)"))
    await conn.execute(
        text(
            "CREATE TABLE IF NOT EXISTS direct_subscriptions ("
            "id SERIAL PRIMARY KEY, "
            "user_id INTEGER NOT NULL REFERENCES users(id), "
            "node_id VARCHAR(64) NOT NULL REFERENCES direct_nodes(id), "
            "subscription_token VARCHAR(128) NOT NULL, "
            "subscription_url TEXT NOT NULL, "
            "status VARCHAR(32) DEFAULT 'active', "
            "created_at TIMESTAMP WITH TIME ZONE NOT NULL, "
            "updated_at TIMESTAMP WITH TIME ZONE NOT NULL, "
            "CONSTRAINT uq_direct_subscriptions_token UNIQUE (subscription_token)"
            ")"
        )
    )
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_direct_subscriptions_user_id ON direct_subscriptions (user_id)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_direct_subscriptions_node_id ON direct_subscriptions (node_id)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_direct_subscriptions_subscription_token ON direct_subscriptions (subscription_token)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_direct_subscriptions_status ON direct_subscriptions (status)"))
    await conn.execute(
        text(
            "CREATE TABLE IF NOT EXISTS subscription_reminders ("
            "id SERIAL PRIMARY KEY, "
            "subscription_id INTEGER NOT NULL REFERENCES subscriptions(id), "
            "user_id INTEGER NOT NULL REFERENCES users(id), "
            "reminder_type VARCHAR(64) NOT NULL, "
            "sent_at TIMESTAMP WITH TIME ZONE NOT NULL, "
            "response VARCHAR(64), "
            "responded_at TIMESTAMP WITH TIME ZONE, "
            "CONSTRAINT uq_subscription_reminders_type UNIQUE (subscription_id, reminder_type)"
            ")"
        )
    )
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_subscription_reminders_subscription_id ON subscription_reminders (subscription_id)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_subscription_reminders_user_id ON subscription_reminders (user_id)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_subscription_reminders_reminder_type ON subscription_reminders (reminder_type)"))
    await conn.execute(
        text(
            "CREATE TABLE IF NOT EXISTS user_feedback ("
            "id SERIAL PRIMARY KEY, "
            "user_id INTEGER NOT NULL REFERENCES users(id), "
            "subscription_id INTEGER REFERENCES subscriptions(id), "
            "reminder_id INTEGER REFERENCES subscription_reminders(id), "
            "source VARCHAR(64) NOT NULL DEFAULT 'trial_feedback', "
            "rating VARCHAR(64), "
            "text TEXT, "
            "created_at TIMESTAMP WITH TIME ZONE NOT NULL, "
            "sent_to_admin_at TIMESTAMP WITH TIME ZONE"
            ")"
        )
    )
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_user_feedback_user_id ON user_feedback (user_id)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_user_feedback_subscription_id ON user_feedback (subscription_id)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_user_feedback_reminder_id ON user_feedback (reminder_id)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_user_feedback_source ON user_feedback (source)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_user_feedback_rating ON user_feedback (rating)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_user_feedback_created_at ON user_feedback (created_at)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_user_feedback_sent_to_admin_at ON user_feedback (sent_to_admin_at)"))
    await conn.execute(text("ALTER TABLE vpn_keys ADD COLUMN IF NOT EXISTS node_id INTEGER"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_vpn_keys_node_id ON vpn_keys (node_id)"))
    await conn.execute(text("ALTER TABLE vpn_keys ADD COLUMN IF NOT EXISTS x3ui_sub_id VARCHAR(64)"))
    await conn.execute(text("ALTER TABLE vpn_keys ADD COLUMN IF NOT EXISTS x3ui_inbound_ids JSON"))
    await conn.execute(text("ALTER TABLE vpn_keys ADD COLUMN IF NOT EXISTS server_label VARCHAR(255)"))
    await conn.execute(text("ALTER TABLE vpn_keys ADD COLUMN IF NOT EXISTS limit_ip INTEGER"))
    await conn.execute(
        text(
            "CREATE TABLE IF NOT EXISTS user_key_activity_snapshots ("
            "id SERIAL PRIMARY KEY, "
            "user_id INTEGER NOT NULL REFERENCES users(id), "
            "key_id INTEGER REFERENCES vpn_keys(id), "
            "node_id VARCHAR(64), "
            "sampled_at TIMESTAMP WITH TIME ZONE NOT NULL, "
            "online BOOLEAN NOT NULL DEFAULT FALSE, "
            "up_bytes BIGINT NOT NULL DEFAULT 0, "
            "down_bytes BIGINT NOT NULL DEFAULT 0, "
            "inbound_count INTEGER NOT NULL DEFAULT 0, "
            "active_inbounds INTEGER NOT NULL DEFAULT 0, "
            "matched_clients INTEGER NOT NULL DEFAULT 0, "
            "source VARCHAR(32) NOT NULL DEFAULT 'detail', "
            "error TEXT"
            ")"
        )
    )
    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_user_key_activity_snapshots_user_id ON user_key_activity_snapshots (user_id)")
    )
    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_user_key_activity_snapshots_key_id ON user_key_activity_snapshots (key_id)")
    )
    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_user_key_activity_snapshots_node_id ON user_key_activity_snapshots (node_id)")
    )
    await conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_user_key_activity_snapshots_sampled_at "
            "ON user_key_activity_snapshots (sampled_at)"
        )
    )
    await conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_user_key_activity_snapshots_user_sampled "
            "ON user_key_activity_snapshots (user_id, sampled_at)"
        )
    )
    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_user_key_activity_snapshots_online ON user_key_activity_snapshots (online)")
    )
    await conn.execute(
        text(
            "DO $$ BEGIN "
            "IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'vpn_keys_node_id_fkey') THEN "
            "ALTER TABLE vpn_keys ADD CONSTRAINT vpn_keys_node_id_fkey FOREIGN KEY (node_id) REFERENCES vpn_nodes(id); "
            "END IF; "
            "END $$;"
        )
    )
    await conn.execute(
        text("ALTER TABLE orders ADD COLUMN IF NOT EXISTS payment_provider VARCHAR(32) DEFAULT 'donationalerts'")
    )
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_orders_payment_provider ON orders (payment_provider)"))
    await conn.execute(text("ALTER TABLE orders ADD COLUMN IF NOT EXISTS moderation_status VARCHAR(32)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_orders_moderation_status ON orders (moderation_status)"))
    await conn.execute(text("ALTER TABLE orders ADD COLUMN IF NOT EXISTS moderation_note TEXT"))
    await conn.execute(text("ALTER TABLE orders ADD COLUMN IF NOT EXISTS manual_qr_file_id VARCHAR(255)"))
    await conn.execute(text("ALTER TABLE orders ADD COLUMN IF NOT EXISTS provisional_expires_at TIMESTAMP WITH TIME ZONE"))
    await conn.execute(text("ALTER TABLE vpn_nodes ADD COLUMN IF NOT EXISTS status VARCHAR(32) DEFAULT 'unknown'"))
    await conn.execute(text("ALTER TABLE vpn_nodes ADD COLUMN IF NOT EXISTS last_checked_at TIMESTAMP WITH TIME ZONE"))
    await conn.execute(text("ALTER TABLE vpn_nodes ADD COLUMN IF NOT EXISTS last_error TEXT"))
    await conn.execute(text("ALTER TABLE vpn_nodes ADD COLUMN IF NOT EXISTS last_latency_ms INTEGER"))
    await conn.execute(text("ALTER TABLE vpn_nodes ADD COLUMN IF NOT EXISTS remote_clients INTEGER DEFAULT 0"))
    await conn.execute(text("ALTER TABLE vpn_nodes ADD COLUMN IF NOT EXISTS remote_enabled_clients INTEGER DEFAULT 0"))
    await conn.execute(text("ALTER TABLE vpn_nodes ADD COLUMN IF NOT EXISTS traffic_up_bytes BIGINT DEFAULT 0"))
    await conn.execute(text("ALTER TABLE vpn_nodes ADD COLUMN IF NOT EXISTS traffic_down_bytes BIGINT DEFAULT 0"))
    await conn.execute(text("ALTER TABLE vpn_nodes ADD COLUMN IF NOT EXISTS cpu_percent INTEGER"))
    await conn.execute(text("ALTER TABLE vpn_nodes ADD COLUMN IF NOT EXISTS memory_percent INTEGER"))
    await conn.execute(text("ALTER TABLE vpn_nodes ADD COLUMN IF NOT EXISTS disk_percent INTEGER"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_vpn_nodes_status ON vpn_nodes (status)"))
    await conn.execute(
        text(
            "DO $$ BEGIN "
            "IF NOT EXISTS ("
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'orders' AND column_name = 'notified_at'"
            ") THEN "
            "ALTER TABLE orders ADD COLUMN notified_at TIMESTAMP WITH TIME ZONE; "
            "UPDATE orders SET notified_at = paid_at WHERE status = 'paid'; "
            "END IF; "
            "END $$;"
        )
    )
