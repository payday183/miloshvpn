from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.models import Subscription, User, VpnKey
from app.services.system_x3ui import select_inbounds_for_client
from app.services.x3ui import X3UIClient
from app.timeutils import utcnow

TRIAL_PLAN_CODE = "trial"


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
            .where(
                VpnKey.user_id == user_id,
                VpnKey.key_type == "private",
                VpnKey.active.is_(True),
            )
        )
    ).all()
    now = utcnow()
    for key in keys:
        x3ui = X3UIClient()
        await x3ui.revoke_client(client_uuid=key.x3ui_client_uuid, email=key.email)
        key.active = False
        key.revoked_at = now


async def list_active_private_keys(session: AsyncSession, limit: int = 30) -> list[VpnKey]:
    return list(
        (
            await session.scalars(
                select(VpnKey)
                .options(
                    selectinload(VpnKey.user),
                    selectinload(VpnKey.subscription),
                )
                .where(VpnKey.key_type == "private", VpnKey.active.is_(True))
                .order_by(VpnKey.created_at.desc())
                .limit(limit)
            )
        ).all()
    )


async def revoke_private_key(session: AsyncSession, key_id: int) -> VpnKey | None:
    key = await session.scalar(
        select(VpnKey)
        .where(VpnKey.id == key_id, VpnKey.key_type == "private", VpnKey.active.is_(True))
        .with_for_update()
    )
    if key is None:
        return None

    x3ui = X3UIClient()
    await x3ui.revoke_client(client_uuid=key.x3ui_client_uuid, email=key.email)
    key.active = False
    key.revoked_at = utcnow()
    return key


async def create_private_key(session: AsyncSession, user: User, subscription: Subscription) -> VpnKey:
    settings = get_settings()
    selection = await select_inbounds_for_client("user")

    await revoke_user_private_keys(session, user.id)

    label = f"milosh_{user.telegram_id}"
    client = await X3UIClient(settings).create_subscription_client(
        email=label,
        telegram_id=user.telegram_id,
        expires_at=subscription.expires_at,
        traffic_gb=subscription.traffic_limit_gb,
        inbound_ids=selection.inbound_ids,
        limit_ip=settings.x3ui_user_limit_ip,
    )
    key = VpnKey(
        node_id=None,
        user_id=user.id,
        subscription_id=subscription.id,
        key_type="private",
        x3ui_client_uuid=client.client_uuid,
        x3ui_sub_id=client.sub_id,
        x3ui_inbound_ids=list(client.inbound_ids),
        server_label=selection.title,
        limit_ip=settings.x3ui_user_limit_ip,
        email=client.email,
        vless_uri=client.vless_uri,
        active=True,
        created_at=utcnow(),
        expires_at=subscription.expires_at,
    )
    session.add(key)
    await session.flush()
    return key


async def replace_active_private_key(session: AsyncSession, user: User) -> VpnKey:
    subscription = await get_active_subscription(session, user.id)
    if subscription is None:
        raise RuntimeError("No active subscription for key replacement")
    return await create_private_key(session, user, subscription)


async def extend_active_subscription_days(session: AsyncSession, user_id: int, days: int) -> Subscription | None:
    now = utcnow()
    subscription = await session.scalar(
        select(Subscription)
        .where(
            Subscription.user_id == user_id,
            Subscription.status == "active",
            Subscription.expires_at > now,
        )
        .order_by(Subscription.expires_at.desc())
        .with_for_update()
    )
    if subscription is None:
        return None

    reward_days = max(1, int(days))
    subscription.expires_at = max(subscription.expires_at, now) + timedelta(days=reward_days)

    keys = (
        await session.scalars(
            select(VpnKey)
            .where(
                VpnKey.subscription_id == subscription.id,
                VpnKey.key_type == "private",
                VpnKey.active.is_(True),
            )
            .with_for_update()
        )
    ).all()
    for key in keys:
        await X3UIClient().update_client_expiry(
            client_uuid=key.x3ui_client_uuid,
            email=key.email,
            expires_at=subscription.expires_at,
            inbound_ids=key.x3ui_inbound_ids or None,
        )
        key.expires_at = subscription.expires_at

    await session.flush()
    return subscription


async def ensure_trial_subscription(session: AsyncSession, user: User) -> tuple[Subscription | None, VpnKey | None, bool]:
    settings = get_settings()
    active_subscription = await get_active_subscription(session, user.id)
    active_key = await get_active_key(session, user.id)
    if active_subscription is not None:
        return active_subscription, active_key, False

    if not settings.free_trial_enabled:
        return None, None, False

    existing_trial = await session.scalar(
        select(Subscription.id).where(Subscription.user_id == user.id, Subscription.plan_code == TRIAL_PLAN_CODE)
    )
    if existing_trial is not None:
        return None, None, False

    days = max(1, settings.free_trial_days)
    traffic_gb = max(1, settings.free_trial_traffic_gb)
    now = utcnow()
    subscription = Subscription(
        user_id=user.id,
        plan_code=TRIAL_PLAN_CODE,
        status="active",
        starts_at=now,
        expires_at=now + timedelta(days=days),
        traffic_limit_gb=traffic_gb,
    )
    session.add(subscription)
    await session.flush()
    key = await create_private_key(session, user, subscription)
    return subscription, key, True


async def create_or_extend_subscription(session: AsyncSession, user: User, plan_code: str) -> Subscription:
    from app.models import Plan

    plan = await session.get(Plan, plan_code)
    if plan is None or not plan.is_active:
        raise RuntimeError(f"Plan is not available: {plan_code}")

    now = utcnow()
    subscription = await get_active_subscription(session, user.id)
    if subscription is not None and subscription.plan_code == TRIAL_PLAN_CODE:
        subscription.status = "upgraded"
        subscription = None

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
