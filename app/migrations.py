from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def run_lightweight_migrations(conn: AsyncConnection) -> None:
    await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS channel_gate_required BOOLEAN DEFAULT FALSE"))
    await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS channel_gate_completed_at TIMESTAMP WITH TIME ZONE"))
    await conn.execute(text("UPDATE users SET channel_gate_required = FALSE WHERE channel_gate_required IS NULL"))
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
    await conn.execute(text("ALTER TABLE vpn_keys ADD COLUMN IF NOT EXISTS node_id INTEGER"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_vpn_keys_node_id ON vpn_keys (node_id)"))
    await conn.execute(text("ALTER TABLE vpn_keys ADD COLUMN IF NOT EXISTS x3ui_sub_id VARCHAR(64)"))
    await conn.execute(text("ALTER TABLE vpn_keys ADD COLUMN IF NOT EXISTS x3ui_inbound_ids JSON"))
    await conn.execute(text("ALTER TABLE vpn_keys ADD COLUMN IF NOT EXISTS server_label VARCHAR(255)"))
    await conn.execute(text("ALTER TABLE vpn_keys ADD COLUMN IF NOT EXISTS limit_ip INTEGER"))
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
