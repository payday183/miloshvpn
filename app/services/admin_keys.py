import secrets

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import User, VpnKey
from app.services.nodes import select_node_for_key
from app.services.x3ui import X3UIClient
from app.timeutils import utcnow


async def create_admin_key(session: AsyncSession, user: User) -> VpnKey:
    settings = get_settings()
    now = utcnow()
    node = await select_node_for_key(session)
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
