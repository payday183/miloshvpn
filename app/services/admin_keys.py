import secrets
from dataclasses import replace

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import User, VpnKey
from app.services.nodes import select_admin_node
from app.services.x3ui import X3UIClient
from app.timeutils import utcnow


async def create_admin_key(session: AsyncSession, user: User) -> VpnKey:
    settings = get_settings()
    now = utcnow()
    node = await select_admin_node(session)
    if node is None and settings.x3ui_mode != "mock":
        raise RuntimeError("No available VPN node for admin key")

    label = f"milosh_admin_{user.telegram_id}_{secrets.token_hex(3)}"
    client = await X3UIClient(settings, node=node).create_client(
        email=label,
        telegram_id=user.telegram_id,
        expires_at=None,
        traffic_gb=None,
    )
    key = VpnKey(
        node_id=node.id if node else None,
        user_id=user.id,
        subscription_id=None,
        key_type="admin",
        x3ui_client_uuid=client.client_uuid,
        email=client.email,
        vless_uri=client.vless_uri,
        active=True,
        created_at=now,
        expires_at=None,
    )
    session.add(key)
    await session.flush()
    return key


async def create_admin_reality_key(session: AsyncSession, user: User) -> VpnKey:
    settings = get_settings()
    if not settings.admin_reality_vless_query.strip():
        raise RuntimeError("ADMIN_REALITY_VLESS_QUERY is not configured")

    now = utcnow()
    node = await select_admin_node(session)
    if node is None and settings.x3ui_mode != "mock":
        raise RuntimeError("No available admin VPN node for Reality key")

    label = f"milosh_admin_reality_{user.telegram_id}_{secrets.token_hex(3)}"
    x3ui = X3UIClient(settings, node=node)
    x3ui.target = replace(
        x3ui.target,
        inbound_id=settings.admin_reality_inbound_id,
        public_host=settings.admin_reality_public_host.strip() or x3ui.target.public_host,
        public_port=settings.admin_reality_public_port,
        vless_query=settings.admin_reality_vless_query.strip(),
    )
    client = await x3ui.create_client(
        email=label,
        telegram_id=user.telegram_id,
        expires_at=None,
        traffic_gb=None,
    )
    key = VpnKey(
        node_id=node.id if node else None,
        user_id=user.id,
        subscription_id=None,
        key_type="admin",
        x3ui_client_uuid=client.client_uuid,
        email=client.email,
        vless_uri=client.vless_uri,
        active=True,
        created_at=now,
        expires_at=None,
    )
    session.add(key)
    await session.flush()
    return key
