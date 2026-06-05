from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.models import Subscription, User, VpnKey
from app.services.nodes import get_active_node
from app.services.x3ui import X3UIClient
from app.timeutils import utcnow


async def active_private_key_count(session: AsyncSession, node_id: int | None = None) -> int:
    query = select(func.count()).select_from(VpnKey).where(
        VpnKey.key_type == "private",
        VpnKey.active.is_(True),
    )
    if node_id is not None:
        query = query.where(VpnKey.node_id == node_id)
    return int(await session.scalar(query) or 0)


async def get_active_subscription(session: AsyncSession, user_id: int) -> Subscription | None:
    now = utcnow()
    return await session.scalar(
        select(Subscription)
        .where(
            Subscription.user_id == user_id,
            Subscription.status == "active",
            Subscription.expires_at > now,
        )
        .order_by(Subscription.expires_at.desc())
    )


async def get_active_key(session: AsyncSession, user_id: int) -> VpnKey | None:
    now = utcnow()
    return await session.scalar(
        select(VpnKey)
        .where(
            VpnKey.user_id == user_id,
            VpnKey.key_type == "private",
            VpnKey.active.is_(True),
            (VpnKey.expires_at.is_(None)) | (VpnKey.expires_at > now),
        )
        .order_by(VpnKey.created_at.desc())
    )


async def revoke_user_private_keys(session: AsyncSession, user_id: int) -> None:
    keys = (
        await session.scalars(
            select(VpnKey)
            .options(selectinload(VpnKey.node))
            .where(
                VpnKey.user_id == user_id,
                VpnKey.key_type == "private",
                VpnKey.active.is_(True),
            )
        )
    ).all()
    now = utcnow()
    for key in keys:
        x3ui = X3UIClient(node=key.node)
        await x3ui.revoke_client(client_uuid=key.x3ui_client_uuid)
        key.active = False
        key.revoked_at = now


async def create_private_key(session: AsyncSession, user: User, subscription: Subscription) -> VpnKey:
    settings = get_settings()
    node = await get_active_node(session)
    node_id = node.id if node is not None else None
    max_clients = node.max_clients if node is not None else settings.x3ui_max_clients
    existing_key = await get_active_key(session, user.id)
    if existing_key is None and await active_private_key_count(session, node_id) >= max_clients:
        raise RuntimeError(f"Test 3x-ui limit reached: {max_clients} active private keys")

    await revoke_user_private_keys(session, user.id)

    label = f"milosh_{user.telegram_id}"
    client = await X3UIClient(settings, node=node).create_client(
        email=label,
        telegram_id=user.telegram_id,
        expires_at=subscription.expires_at,
        traffic_gb=subscription.traffic_limit_gb,
    )
    key = VpnKey(
        node_id=node_id,
        user_id=user.id,
        subscription_id=subscription.id,
        key_type="private",
        x3ui_client_uuid=client.client_uuid,
        email=client.email,
        vless_uri=client.vless_uri,
        active=True,
        created_at=utcnow(),
        expires_at=subscription.expires_at,
    )
    session.add(key)
    await session.flush()
    return key


async def create_or_extend_subscription(session: AsyncSession, user: User, plan_code: str) -> Subscription:
    from app.models import Plan

    plan = await session.get(Plan, plan_code)
    if plan is None or not plan.is_active:
        raise RuntimeError(f"Plan is not available: {plan_code}")

    now = utcnow()
    subscription = await get_active_subscription(session, user.id)
    if subscription is None:
        starts_at = now
        expires_at = now + timedelta(days=plan.days)
        subscription = Subscription(
            user_id=user.id,
            plan_code=plan.code,
            status="active",
            starts_at=starts_at,
            expires_at=expires_at,
            traffic_limit_gb=plan.traffic_gb,
        )
        session.add(subscription)
    else:
        base = max(subscription.expires_at, now)
        subscription.plan_code = plan.code
        subscription.expires_at = base + timedelta(days=plan.days)
        subscription.traffic_limit_gb = plan.traffic_gb
        subscription.status = "active"

    await session.flush()
    await create_private_key(session, user, subscription)
    return subscription
