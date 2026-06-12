import secrets
from dataclasses import replace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import BotAdmin, User, VpnKey
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
    now = utcnow()
    node = await select_admin_node(session)
    if node is None and settings.x3ui_mode != "mock":
        raise RuntimeError("No available admin VPN node for Reality key")

    label = f"milosh_admin_reality_{user.telegram_id}_{secrets.token_hex(3)}"
    x3ui = X3UIClient(settings, node=node)
    reality_query = settings.admin_reality_vless_query.strip() or "type=tcp&security=reality&encryption=none"
    x3ui.target = replace(
        x3ui.target,
        inbound_id=settings.admin_reality_inbound_id,
        public_host=settings.admin_reality_public_host.strip() or x3ui.target.public_host,
        public_port=settings.admin_reality_public_port,
        vless_query=reality_query,
        public_key=settings.admin_reality_public_key.strip(),
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


async def create_admin_reality_keys_for_admins(session: AsyncSession) -> list[tuple[int, VpnKey]]:
    admin_ids = await list_admin_telegram_ids(session)
    if not admin_ids:
        raise RuntimeError("No admins configured for Reality test keys")

    created: list[tuple[int, VpnKey]] = []
    for telegram_id in admin_ids:
        user = await get_or_create_admin_user(session, telegram_id)
        key = await create_admin_reality_key(session, user)
        created.append((telegram_id, key))
    return created


async def list_admin_telegram_ids(session: AsyncSession) -> list[int]:
    settings = get_settings()
    db_admins = (await session.scalars(select(BotAdmin.telegram_id).order_by(BotAdmin.telegram_id))).all()
    admin_ids = [int(admin_id) for admin_id in settings.admin_ids]
    admin_ids.extend(int(admin_id) for admin_id in db_admins)
    return list(dict.fromkeys(admin_ids))


async def get_or_create_admin_user(session: AsyncSession, telegram_id: int) -> User:
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        user = User(
            telegram_id=telegram_id,
            username=None,
            first_name=None,
            role="admin",
            created_at=utcnow(),
        )
        session.add(user)
    else:
        user.role = "admin"

    existing_admin = await session.scalar(select(BotAdmin).where(BotAdmin.telegram_id == telegram_id))
    if existing_admin is None:
        session.add(BotAdmin(telegram_id=telegram_id, added_at=utcnow()))

    await session.flush()
    return user
