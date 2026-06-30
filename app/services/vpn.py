import asyncio
from datetime import timedelta
import logging
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.models import Subscription, User, VpnKey
from app.services.system_x3ui import select_inbounds_for_client
from app.services.x3ui import X3UIClient
from app.timeutils import utcnow

TRIAL_PLAN_CODE = "trial"
logger = logging.getLogger(__name__)


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
        await revoke_key_remote(session, key)
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

    await revoke_key_remote(session, key)
    key.active = False
    key.revoked_at = utcnow()
    return key


async def create_private_key(session: AsyncSession, user: User, subscription: Subscription) -> VpnKey:
    settings = get_settings()
    from app.services.direct_node_admin import create_or_replace_user_direct_key

    if settings.x3ui_mode == "mock":
        return await create_system_private_key(session, user, subscription)

    candidates = await healthy_user_node_candidates(session)
    errors: list[str] = []
    for candidate in candidates:
        savepoint = await session.begin_nested()
        try:
            key = await create_or_replace_user_direct_key(
                session,
                user,
                subscription,
                node_config=candidate["config"],
                require_flags=False,
            )
            await savepoint.commit()
            return key
        except Exception as exc:
            await savepoint.rollback()
            errors.append(f"{candidate['name']}: {str(exc)[:160]}")
            logger.exception("Private-key provisioning failed on node %s", candidate["name"])
    raise RuntimeError("No VPN server accepted the key: " + "; ".join(errors))


async def create_system_private_key(
    session: AsyncSession,
    user: User,
    subscription: Subscription,
    *,
    selection=None,
    revoke_existing: bool = True,
) -> VpnKey:
    """Issue a key on the least-loaded node managed by the system 3x-ui panel."""
    settings = get_settings()

    selection = selection or await select_inbounds_for_client("user")

    label = f"milosh_{user.telegram_id}_{uuid4().hex[:8]}"
    client = await X3UIClient(settings).create_subscription_client(
        email=label,
        telegram_id=user.telegram_id,
        expires_at=subscription.expires_at,
        traffic_gb=subscription.traffic_limit_gb,
        inbound_ids=selection.inbound_ids,
        limit_ip=settings.x3ui_user_limit_ip,
    )
    if revoke_existing:
        await revoke_user_private_keys(session, user.id)
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

    current_key = await get_active_key(session, user.id)
    if current_key is None:
        return await create_private_key(session, user, subscription)

    candidates = await replacement_backend_candidates(session, current_key)
    errors: list[str] = []
    new_key: VpnKey | None = None
    selected_backend = ""
    for candidate in candidates:
        savepoint = await session.begin_nested()
        try:
            from app.services.direct_node_admin import create_or_replace_user_direct_key

            new_key = await create_or_replace_user_direct_key(
                session,
                user,
                subscription,
                node_config=candidate["config"],
                require_flags=False,
                revoke_existing=False,
            )
            await savepoint.commit()
            selected_backend = str(candidate["name"])
            break
        except Exception as exc:
            await savepoint.rollback()
            errors.append(f"{candidate['name']}: {str(exc)[:160]}")
            logger.exception("Replacement provisioning failed on backend %s", candidate["name"])

    if new_key is None:
        raise RuntimeError("No VPN server accepted replacement: " + "; ".join(errors))

    current_backend = await key_backend_name(session, current_key)
    now = utcnow()
    try:
        await revoke_key_remote(session, current_key)
    except Exception:
        current_key.revoked_at = now
        logger.exception("Immediate old-key cleanup failed; scheduled for retry: key_id=%s", current_key.id)
    else:
        current_key.active = False
        current_key.revoked_at = now
        if current_backend != selected_backend:
            await release_slots_after_backend_move(session, current_key, new_key)

    await session.flush()
    return new_key


async def key_backend_name(session: AsyncSession, key: VpnKey) -> str:
    from app.services.direct_node_admin import direct_node_id_for_key, is_direct_vpn_key

    if not is_direct_vpn_key(key):
        # Legacy private keys were issued on the France group of the system panel.
        return "fr-1" if key.key_type == "private" else "system"
    return await direct_node_id_for_key(session, key) or "direct"


async def healthy_user_node_candidates(
    session: AsyncSession,
    *,
    exclude_node_id: str | None = None,
) -> list[dict[str, object]]:
    from app.services.direct_node_admin import list_direct_node_configs, x3ui_client_for_config

    settings = get_settings()
    configs = [
        config
        for config in await list_direct_node_configs(session)
        if config.node_id != exclude_node_id
        and config.api_base_url.strip()
        and (config.api_token.strip() or (config.api_username.strip() and config.api_password.strip()))
        and (config.public_host.strip() or config.public_ip.strip())
        and config.status != "offline"
    ]
    health = await asyncio.gather(
        *(x3ui_client_for_config(config, settings).list_inbounds() for config in configs),
        return_exceptions=True,
    )

    active_keys = (
        await session.execute(
            select(VpnKey.user_id, VpnKey.email).where(
                VpnKey.active.is_(True),
                VpnKey.key_type == "private",
                VpnKey.user_id.is_not(None),
            )
        )
    ).all()
    users_by_node: dict[str, set[int]] = {config.node_id: set() for config in configs}
    known_node_ids = tuple(config.node_id for config in configs)
    for user_id, email in active_keys:
        matched_node_id = next(
            (node_id for node_id in known_node_ids if str(email).endswith(f"_{node_id}")),
            None,
        )
        if matched_node_id is None and "fr-1" in users_by_node and str(email).startswith("milosh_"):
            matched_node_id = "fr-1"
        if matched_node_id is not None:
            users_by_node[matched_node_id].add(int(user_id))

    candidates: list[dict[str, object]] = []
    errors: list[str] = []
    for config, result in zip(configs, health, strict=True):
        if isinstance(result, Exception):
            errors.append(f"{config.node_id}: {str(result)[:160]}")
            continue
        candidates.append(
            {
                "name": config.node_id,
                "load": len(users_by_node.get(config.node_id, set())),
                "config": config,
            }
        )

    if not candidates:
        raise RuntimeError("No healthy VPN servers: " + "; ".join(errors))

    return sorted(candidates, key=lambda item: (int(item["load"]), str(item["name"])))


async def replacement_backend_candidates(session: AsyncSession, current_key: VpnKey) -> list[dict[str, object]]:
    current_backend = await key_backend_name(session, current_key)
    return await healthy_user_node_candidates(session, exclude_node_id=current_backend)


async def release_slots_after_backend_move(session: AsyncSession, old_key: VpnKey, new_key: VpnKey) -> None:
    from app.services.direct_node_admin import (
        direct_node_id_for_key,
        is_direct_vpn_key,
        release_direct_key_slots,
    )

    if not is_direct_vpn_key(old_key):
        return
    old_node_id = await direct_node_id_for_key(session, old_key)
    new_node_id = await direct_node_id_for_key(session, new_key) if is_direct_vpn_key(new_key) else None
    if old_node_id is not None and old_node_id == new_node_id:
        return

    settings = get_settings()
    await release_direct_key_slots(
        session,
        old_key,
        status="moved",
        release_slots=True,
        delete_remote_inbounds=settings.inbound_delete_on_user_move,
    )


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
        await update_key_expiry_remote(session, key, subscription.expires_at)
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


async def create_or_extend_subscription(
    session: AsyncSession,
    user: User,
    plan_code: str,
    *,
    issue_key: bool = True,
) -> Subscription:
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
    if issue_key:
        await create_private_key(session, user, subscription)
    return subscription


async def revoke_key_remote(session: AsyncSession, key: VpnKey) -> None:
    from app.services.direct_node_admin import is_direct_vpn_key, revoke_key_remote as revoke_direct_key_remote

    if is_direct_vpn_key(key):
        await revoke_direct_key_remote(session, key)
        return
    await X3UIClient().revoke_client(client_uuid=key.x3ui_client_uuid, email=key.email)


async def update_key_expiry_remote(session: AsyncSession, key: VpnKey, expires_at) -> None:
    from app.services.direct_node_admin import direct_node_config_for_key, is_direct_vpn_key, x3ui_client_for_config

    if is_direct_vpn_key(key):
        settings = get_settings()
        config = await direct_node_config_for_key(session, key)
        if config is None:
            raise RuntimeError("Direct-node config for key was not found")
        await x3ui_client_for_config(config).update_client_expiry(
            client_uuid=key.x3ui_client_uuid,
            email=key.email,
            expires_at=expires_at,
            inbound_ids=key.x3ui_inbound_ids or None,
            limit_ip=settings.direct_node_user_limit_ip,
        )
        key.limit_ip = settings.direct_node_user_limit_ip
        return
    await X3UIClient().update_client_expiry(
        client_uuid=key.x3ui_client_uuid,
        email=key.email,
        expires_at=expires_at,
        inbound_ids=key.x3ui_inbound_ids or None,
    )
