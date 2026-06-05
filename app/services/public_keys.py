from datetime import timedelta
from html import escape

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.models import VpnKey
from app.services.nodes import get_active_node
from app.services.x3ui import X3UIClient
from app.timeutils import utcnow


async def get_active_public_key(session: AsyncSession) -> VpnKey | None:
    now = utcnow()
    return await session.scalar(
        select(VpnKey)
        .where(
            VpnKey.key_type == "public",
            VpnKey.active.is_(True),
            (VpnKey.expires_at.is_(None)) | (VpnKey.expires_at > now),
        )
        .order_by(VpnKey.created_at.desc())
    )


async def rotate_public_key(session: AsyncSession) -> VpnKey:
    settings = get_settings()
    now = utcnow()
    node = await get_active_node(session)

    keys = (
        await session.scalars(
            select(VpnKey)
            .options(selectinload(VpnKey.node))
            .where(VpnKey.key_type == "public", VpnKey.active.is_(True))
        )
    ).all()
    for key in keys:
        x3ui = X3UIClient(settings, node=key.node)
        await x3ui.revoke_client(client_uuid=key.x3ui_client_uuid)
        key.active = False
        key.revoked_at = now

    expires_at = now + timedelta(hours=settings.public_key_rotate_hours)
    x3ui = X3UIClient(settings, node=node)
    client = await x3ui.create_client(
        email=f"milosh_free_{now:%Y%m%d_%H%M}",
        telegram_id=None,
        expires_at=expires_at,
        traffic_gb=5,
    )
    key = VpnKey(
        node_id=node.id if node else None,
        user_id=None,
        subscription_id=None,
        key_type="public",
        x3ui_client_uuid=client.client_uuid,
        email=client.email,
        vless_uri=client.vless_uri,
        active=True,
        created_at=now,
        expires_at=expires_at,
    )
    session.add(key)
    await session.commit()
    await session.refresh(key)
    return key


def public_key_post_text(key: VpnKey) -> str:
    expires = key.expires_at.strftime("%d.%m %H:%M UTC") if key.expires_at else "через 24 часа"
    vless_uri = escape(key.vless_uri)
    return (
        "Бесплатный ключ MiloshVPN уже на столе.\n\n"
        "Забирай VLESS, проверяй скорость и не рассказывай интернету, что он был медленным.\n\n"
        f"<code>{vless_uri}</code>\n\n"
        f"Живет до: {expires}\n"
        "Через 24 часа ключ будет заменен автоматически."
    )
