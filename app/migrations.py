from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def run_lightweight_migrations(conn: AsyncConnection) -> None:
    await conn.execute(text("ALTER TABLE vpn_keys ADD COLUMN IF NOT EXISTS node_id INTEGER"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_vpn_keys_node_id ON vpn_keys (node_id)"))
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
