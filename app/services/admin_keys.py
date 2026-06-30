import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import BotAdmin, User, VpnKey
from app.services.system_x3ui import select_inbounds_for_client
from app.services.x3ui import X3UIClient
from app.timeutils import utcnow


async def create_admin_key(session: AsyncSession, user: User) -> VpnKey:
    settings = get_settings()
    if settings.x3ui_mode != "mock":
        from app.services.direct_node_admin import create_or_replace_admin_direct_key, direct_node_config_by_id

        node_id = "nl-local"
        node_config = await direct_node_config_by_id(session, node_id)
        if node_config is None and settings.admin_direct_node_id.strip():
            node_id = settings.admin_direct_node_id.strip()
            node_config = await direct_node_config_by_id(session, node_id)
        if node_config is None:
            raise RuntimeError(f"Admin direct node was not found: {node_id}")
        if node_config.country_code.upper() != "NL":
            raise RuntimeError("Admin keys must be issued on the Netherlands node")
        return await create_or_replace_admin_direct_key(session, user, node_config)

    now = utcnow()
    selection = await select_inbounds_for_client("admin")

    label = f"milosh_admin_{user.telegram_id}_{secrets.token_hex(3)}"
    client = await X3UIClient(settings).create_subscription_client(
        email=label,
        telegram_id=user.telegram_id,
        expires_at=None,
        traffic_gb=None,
        inbound_ids=selection.inbound_ids,
        limit_ip=settings.x3ui_admin_limit_ip,
    )
    key = VpnKey(
        node_id=None,
        user_id=user.id,
        subscription_id=None,
        key_type="admin",
        x3ui_client_uuid=client.client_uuid,
        x3ui_sub_id=client.sub_id,
        x3ui_inbound_ids=list(client.inbound_ids),
        server_label=selection.title,
        limit_ip=settings.x3ui_admin_limit_ip,
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
    selection = await select_inbounds_for_client("admin_reality")

    label = f"milosh_admin_reality_{user.telegram_id}_{secrets.token_hex(3)}"
    client = await X3UIClient(settings).create_subscription_client(
        email=label,
        telegram_id=user.telegram_id,
        expires_at=None,
        traffic_gb=None,
        inbound_ids=selection.inbound_ids,
        limit_ip=settings.x3ui_admin_limit_ip,
    )
    key = VpnKey(
        node_id=None,
        user_id=user.id,
        subscription_id=None,
        key_type="admin",
        x3ui_client_uuid=client.client_uuid,
        x3ui_sub_id=client.sub_id,
        x3ui_inbound_ids=list(client.inbound_ids),
        server_label=selection.title,
        limit_ip=settings.x3ui_admin_limit_ip,
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
