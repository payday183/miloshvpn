from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from html import escape
from typing import Any
from urllib.parse import urlencode

from aiogram import Bot
from aiogram.enums import ParseMode
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db import get_session
from app.models import (
    BotAdmin,
    DirectUserProfile,
    Order,
    Plan,
    Subscription,
    SubscriptionReminder,
    User,
    UserFeedback,
    UserKeyActivitySnapshot,
    VpnKey,
)
from app.services.admin_auth import (
    admin_panel_url_with_token,
    verify_admin_profile_signature,
    verify_telegram_login,
)
from app.services.admin_key_notifications import send_admin_reality_key
from app.services.admin_keys import create_admin_key, create_admin_reality_key
from app.services.billing import poll_donations
from app.services.direct_node_admin import (
    DirectNodeConfig,
    check_direct_node_connection,
    create_or_replace_user_direct_key,
    direct_node_config_by_id,
    direct_node_config_for_key,
    is_direct_vpn_key,
    list_direct_node_configs,
    release_direct_key_slots,
    save_direct_node_config,
    x3ui_client_for_config,
)
from app.services.expiry import expire_subscriptions, retry_expired_key_revokes
from app.services.manual_orders import ManualOrderError, ManualOrderGrantResult, manually_confirm_order, search_orders_for_admin
from app.services.payment_notifications import notify_paid_order
from app.services.payment_modes import (
    get_active_payment_provider,
    list_payment_providers,
    payment_provider_label,
    set_active_payment_provider,
)
from app.services.public_keys import rotate_public_key
from app.services.stats import collect_stats
from app.services.system_x3ui import collect_system_x3ui_state
from app.services.vpn import (
    create_or_extend_subscription,
    create_system_private_key,
    get_active_subscription,
    list_active_private_keys,
    revoke_key_remote,
    revoke_private_key,
)
from app.services.x3ui import X3UIClient
from app.tg.texts import subscription_text
from app.timeutils import utcnow

router = APIRouter()
ACTIVITY_SNAPSHOT_THROTTLE_SECONDS = 120
ACTIVITY_SNAPSHOT_DAYS = 30
SYSTEM_NODE_AUTO = "system:auto"


@dataclass(frozen=True)
class AdminUserMonitorRow:
    user: User
    subscription: Subscription | None
    key: VpnKey | None
    plan_kind: str
    key_state: str
    server_label: str
    direct_profiles: int
    assigned_inbounds: int
    expired_keys: int
    paid_orders: int
    total_orders: int
    feedback_count: int
    last_feedback_rating: str
    last_feedback_at: object | None


@dataclass(frozen=True)
class ActivityRefreshResult:
    snapshot: UserKeyActivitySnapshot | None
    created: bool
    error: str = ""


async def require_admin_token(request: Request) -> None:
    settings = get_settings()
    if not settings.admin_web_token:
        return

    token = request.query_params.get("token") or request.headers.get("X-Admin-Token")
    if token != settings.admin_web_token:
        raise HTTPException(status_code=403, detail="Admin token required")


@router.get("/admin/profile", response_class=HTMLResponse)
async def admin_profile(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    telegram_id = verify_admin_profile_signature(request.query_params)
    if telegram_id is None:
        return HTMLResponse(render_admin_profile_login_page(request))

    user = await get_admin_user(session, telegram_id)
    if user is None and not await is_admin_telegram_id(session, telegram_id):
        raise HTTPException(status_code=403, detail="Admin access required")

    return HTMLResponse(render_admin_profile_page(telegram_id, user, auth_source="Telegram bot"))


@router.get("/admin/profile/login", response_class=HTMLResponse)
async def admin_profile_login(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    telegram_id = verify_telegram_login(request.query_params)
    if telegram_id is None:
        return HTMLResponse(render_admin_profile_login_page(request, error="Не удалось проверить вход через Telegram."))

    user = await get_admin_user(session, telegram_id)
    if user is None and not await is_admin_telegram_id(session, telegram_id):
        raise HTTPException(status_code=403, detail="Admin access required")

    return HTMLResponse(render_admin_profile_page(telegram_id, user, auth_source="Telegram Login"))


@router.get("/admin", response_class=HTMLResponse)
async def admin_panel(
    request: Request,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin_token),
) -> HTMLResponse:
    token = request.query_params.get("token", "")
    stats = await collect_stats(session)
    system_state = await collect_system_x3ui_state()
    subscriptions = await active_subscriptions(session)
    pending_orders = await latest_pending_orders(session)
    private_keys = await list_active_private_keys(session, limit=100)
    payment_provider = await get_active_payment_provider(session)
    return HTMLResponse(
        render_admin_page(stats, system_state, subscriptions, pending_orders, private_keys, token, payment_provider)
    )


@router.get("/admin/direct-nodes", response_class=HTMLResponse)
async def direct_nodes_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin_token),
) -> HTMLResponse:
    token = request.query_params.get("token", "")
    nodes = await list_direct_node_configs(session)
    return HTMLResponse(render_direct_nodes_page(nodes, token))


@router.get("/admin/users", response_class=HTMLResponse)
async def admin_users_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin_token),
) -> HTMLResponse:
    token = request.query_params.get("token", "")
    rows = await admin_user_monitor_rows(session)
    filtered_rows = filter_admin_user_rows(rows, request.query_params)
    sorted_rows = sort_admin_user_rows(filtered_rows, str(request.query_params.get("sort") or "created_desc"))
    page_rows, page, per_page = paginate_admin_user_rows(sorted_rows, request.query_params)
    nodes = await list_direct_node_configs(session)
    plans = await admin_issue_plans(session)
    return HTMLResponse(
        render_admin_users_page(page_rows, rows, len(sorted_rows), page, per_page, nodes, plans, request, token)
    )


@router.get("/admin/feedback", response_class=HTMLResponse)
async def admin_feedback_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin_token),
) -> HTMLResponse:
    token = request.query_params.get("token", "")
    feedbacks = await admin_feedback_rows(session)
    filtered = filter_admin_feedback_rows(feedbacks, request.query_params)
    return HTMLResponse(render_admin_feedback_page(filtered, feedbacks, request, token))


@router.get("/admin/users/{user_id}", response_class=HTMLResponse)
async def admin_user_detail_page(
    user_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin_token),
) -> HTMLResponse:
    token = request.query_params.get("token", "")
    user = await session.scalar(
        select(User)
        .options(
            selectinload(User.subscriptions),
            selectinload(User.keys),
            selectinload(User.orders),
        )
        .where(User.id == user_id)
    )
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    row = await admin_user_monitor_row_for_user(session, user)
    nodes = await list_direct_node_configs(session)
    plans = await admin_issue_plans(session)
    direct_profiles = await user_direct_profiles(session, user.id)
    feedbacks = await user_feedback_rows(session, user.id)
    activity_result = await refresh_user_activity_snapshot(session, row.key)
    if activity_result.created:
        await session.commit()
    activity_snapshots = await user_activity_snapshots(session, user.id)
    return HTMLResponse(
        render_admin_user_detail_page(
            row,
            direct_profiles,
            nodes,
            plans,
            feedbacks,
            activity_snapshots,
            activity_result,
            request,
            token,
        )
    )


@router.post("/admin/users/{user_id}/delete-access")
async def admin_delete_user_access_action(
    user_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin_token),
) -> RedirectResponse:
    form = await request.form()
    return_to = str(form.get("return_to") or "")
    delete_inbounds = str(form.get("delete_inbounds") or "").lower() in {"1", "true", "on", "yes"}
    result = await delete_user_access(session, user_id, delete_inbounds=delete_inbounds)
    await session.commit()
    token = request.query_params.get("token", "")
    notice = (
        f"Доступ удалён: ключей {result['keys']}, inbound-ов удалено {result['inbounds']}"
        if delete_inbounds
        else f"Ключ удалён: ключей {result['keys']}, inbound-ы оставлены за пользователем"
    )
    if return_to == "detail":
        return redirect_to_admin_user_detail(user_id, token, notice)
    return redirect_to_admin_users(token, notice)


@router.post("/admin/users/{user_id}/issue-key")
async def admin_issue_user_key_action(
    user_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin_token),
) -> RedirectResponse:
    form = await request.form()
    return_to = str(form.get("return_to") or "")
    token = request.query_params.get("token", "")
    node_id = str(form.get("node_id") or SYSTEM_NODE_AUTO).strip()
    plan_code = str(form.get("plan_code") or "").strip()
    user = await session.get(User, user_id)
    if user is None:
        return redirect_to_admin_users(token, "Пользователь не найден")

    try:
        if plan_code:
            subscription = await create_or_extend_subscription(session, user, plan_code, issue_key=False)
        else:
            subscription = await get_active_subscription(session, user.id)
            if subscription is None:
                raise RuntimeError("выберите тариф: у пользователя нет активной подписки")

        if node_id == SYSTEM_NODE_AUTO:
            key = await create_system_private_key(session, user, subscription)
        else:
            node_config = await direct_node_config_by_id(session, node_id)
            if node_config is None:
                raise RuntimeError("узел не найден")
            key = await create_or_replace_user_direct_key(
                session,
                user,
                subscription,
                node_config=node_config,
                require_flags=False,
            )
        await session.commit()
    except Exception as exc:
        await session.rollback()
        notice = f"Ключ не выдан: {str(exc)[:160]}"
        return redirect_to_admin_user_detail(user_id, token, notice) if return_to == "detail" else redirect_to_admin_users(token, notice)

    notified = await notify_manual_key_from_web(user.telegram_id, subscription, key)
    notice = (
        f"Ключ выдан ({subscription.plan_code}) и отправлен пользователю {user.telegram_id}"
        if notified
        else f"Ключ выдан ({subscription.plan_code}) пользователю {user.telegram_id}, но Telegram-сообщение не отправилось"
    )
    return redirect_to_admin_user_detail(user_id, token, notice) if return_to == "detail" else redirect_to_admin_users(token, notice)


@router.post("/admin/direct-nodes/check", response_class=HTMLResponse)
async def check_direct_node_action(
    request: Request,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin_token),
) -> HTMLResponse:
    token = request.query_params.get("token", "")
    form = await request.form()
    config = direct_node_config_from_form(form)
    checks = await check_direct_node_connection(config)
    nodes = await list_direct_node_configs(session)
    return HTMLResponse(render_direct_nodes_page(nodes, token, form_config=config, checks=checks))


@router.post("/admin/direct-nodes", response_class=HTMLResponse)
async def save_direct_node_action(
    request: Request,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin_token),
) -> HTMLResponse:
    token = request.query_params.get("token", "")
    form = await request.form()
    config = direct_node_config_from_form(form)
    checks = await check_direct_node_connection(config)
    if any(check.status == "error" for check in checks):
        nodes = await list_direct_node_configs(session)
        return HTMLResponse(
            render_direct_nodes_page(nodes, token, form_config=config, checks=checks, error="Сначала исправьте ошибки проверки."),
            status_code=400,
        )

    await save_direct_node_config(session, config, status="online")
    await session.commit()
    nodes = await list_direct_node_configs(session)
    return HTMLResponse(render_direct_nodes_page(nodes, token, checks=checks, notice="Узел сохранён."))


@router.post("/admin/donations/poll")
async def poll_donations_action(
    request: Request,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin_token),
) -> RedirectResponse:
    await poll_donations(session)
    return redirect_to_admin(request)


@router.post("/admin/payment-mode")
async def set_payment_mode_action(
    request: Request,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin_token),
) -> RedirectResponse:
    form = await request.form()
    provider = str(form.get("provider") or "").strip()
    try:
        await set_active_payment_provider(session, provider)
        await session.commit()
    except ValueError:
        await session.rollback()
        raise HTTPException(status_code=400, detail="Unknown payment provider")
    return redirect_to_admin(request)


@router.get("/admin/orders/search", response_class=HTMLResponse)
async def search_orders_action(
    request: Request,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin_token),
) -> HTMLResponse:
    query = str(request.query_params.get("query") or "").strip()
    token = request.query_params.get("token", "")
    orders = await search_orders_for_admin(session, query, limit=20) if query else []
    return HTMLResponse(render_order_search_page(query, orders, token))


@router.post("/admin/orders/{order_id}/grant", response_class=HTMLResponse)
async def grant_order_action(
    order_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin_token),
) -> HTMLResponse:
    token = request.query_params.get("token", "")
    try:
        result = await manually_confirm_order(session, order_id, admin_telegram_id=0)
        await session.commit()
    except ManualOrderError as exc:
        await session.rollback()
        return HTMLResponse(render_manual_order_error_page(str(exc), token), status_code=404)
    except Exception:
        await session.rollback()
        raise

    notified = await notify_order_from_web(result.order_id)
    return HTMLResponse(render_manual_order_grant_page(result, notified, token))


@router.post("/admin/public-key/rotate")
async def rotate_public_key_action(
    request: Request,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin_token),
) -> RedirectResponse:
    await rotate_public_key(session)
    return redirect_to_admin(request)


@router.post("/admin/admin-keys")
async def create_admin_key_action(
    request: Request,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin_token),
) -> HTMLResponse:
    form = await request.form()
    telegram_id_raw = str(form.get("telegram_id") or "").strip()
    if not telegram_id_raw.isdigit():
        raise HTTPException(status_code=400, detail="Telegram ID is required")

    telegram_id = int(telegram_id_raw)
    user = await get_or_create_admin_user(session, telegram_id)
    key = await create_admin_key(session, user)
    await session.commit()
    await session.refresh(key)
    return HTMLResponse(render_admin_key_page(key, telegram_id, request.query_params.get("token", "")))


@router.post("/admin/admin-reality-keys")
async def create_admin_reality_key_action(
    request: Request,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin_token),
) -> HTMLResponse:
    form = await request.form()
    telegram_id_raw = str(form.get("telegram_id") or "").strip()
    if not telegram_id_raw.isdigit():
        raise HTTPException(status_code=400, detail="Telegram ID is required")

    telegram_id = int(telegram_id_raw)
    user = await get_or_create_admin_user(session, telegram_id)
    key = await create_admin_reality_key(session, user)
    await session.commit()
    await session.refresh(key)
    await notify_admin_reality_key_from_web(telegram_id, key)
    return HTMLResponse(render_admin_key_page(key, telegram_id, request.query_params.get("token", ""), reality=True))


@router.post("/admin/keys/{key_id}/revoke")
async def revoke_private_key_action(
    key_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin_token),
) -> RedirectResponse:
    await revoke_private_key(session, key_id)
    await session.commit()
    return redirect_to_admin(request)


@router.post("/admin/subscriptions/cleanup-expired")
async def cleanup_expired_subscriptions_action(
    request: Request,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin_token),
) -> RedirectResponse:
    await expire_subscriptions(session)
    await retry_expired_key_revokes(session)
    return redirect_to_admin(request)


async def notify_order_from_web(order_id: int) -> bool:
    settings = get_settings()
    if not settings.bot_token:
        return False

    bot = Bot(settings.bot_token)
    try:
        return await notify_paid_order(bot, order_id, force=True)
    finally:
        await bot.session.close()


async def notify_admin_reality_key_from_web(telegram_id: int, key: VpnKey) -> bool:
    settings = get_settings()
    if not settings.bot_token:
        return False

    bot = Bot(settings.bot_token)
    try:
        return await send_admin_reality_key(bot, telegram_id, key)
    finally:
        await bot.session.close()


async def notify_manual_key_from_web(telegram_id: int, subscription: Subscription, key: VpnKey) -> bool:
    settings = get_settings()
    if not settings.bot_token:
        return False

    bot = Bot(settings.bot_token)
    try:
        await bot.send_message(
            telegram_id,
            "🔑 Администратор выдал вам новый sub.\n\n" + subscription_text(subscription, key),
            parse_mode=ParseMode.HTML,
        )
        return True
    except Exception:
        return False
    finally:
        await bot.session.close()


@router.get("/api/admin/subscriptions")
async def api_subscriptions(
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin_token),
) -> list[dict[str, object]]:
    rows = await active_subscriptions(session)
    return [serialize_subscription(subscription, key) for subscription, key in rows]


async def admin_user_monitor_rows(session: AsyncSession) -> list[AdminUserMonitorRow]:
    now = utcnow()
    users = (
        await session.scalars(
            select(User)
            .options(
                selectinload(User.subscriptions),
                selectinload(User.keys),
                selectinload(User.orders),
            )
            .order_by(User.created_at.desc())
        )
    ).all()

    profile_counts = Counter()
    profiles = (
        await session.scalars(
            select(DirectUserProfile).where(DirectUserProfile.status == "active")
        )
    ).all()
    for profile in profiles:
        profile_counts[profile.user_id] += 1

    feedback_counts = Counter()
    latest_feedback_by_user: dict[int, UserFeedback] = {}
    feedbacks = (
        await session.scalars(select(UserFeedback).order_by(UserFeedback.created_at.asc(), UserFeedback.id.asc()))
    ).all()
    for feedback in feedbacks:
        feedback_counts[feedback.user_id] += 1
        latest_feedback_by_user[feedback.user_id] = feedback

    rows: list[AdminUserMonitorRow] = []
    for user in users:
        subscription = latest_user_subscription(user, now)
        key = latest_user_key(user, now)
        active_key = key is not None and key.active and (key.expires_at is None or key.expires_at > now)
        expired_keys = sum(
            1
            for item in user.keys
            if item.key_type == "private" and (not item.active or (item.expires_at is not None and item.expires_at <= now))
        )
        paid_orders = sum(1 for order in user.orders if order.status == "paid")
        total_orders = len(user.orders)
        direct_profiles = profile_counts[user.id]
        inbound_count = len(key.x3ui_inbound_ids or []) if key is not None else 0
        plan_kind = plan_kind_for_subscription(subscription, now)
        key_state = "active" if active_key else "expired" if key is not None else "no_key"
        latest_feedback = latest_feedback_by_user.get(user.id)
        rows.append(
            AdminUserMonitorRow(
                user=user,
                subscription=subscription,
                key=key,
                plan_kind=plan_kind,
                key_state=key_state,
                server_label=key.server_label if key and key.server_label else "нет ключа",
                direct_profiles=direct_profiles,
                assigned_inbounds=inbound_count or direct_profiles,
                expired_keys=expired_keys,
                paid_orders=paid_orders,
                total_orders=total_orders,
                feedback_count=feedback_counts[user.id],
                last_feedback_rating=latest_feedback.rating if latest_feedback and latest_feedback.rating else "",
                last_feedback_at=latest_feedback.created_at if latest_feedback else None,
            )
        )
    return rows


async def admin_user_monitor_row_for_user(session: AsyncSession, user: User) -> AdminUserMonitorRow:
    now = utcnow()
    direct_profiles = int(
        await session.scalar(
            select(func.count())
            .select_from(DirectUserProfile)
            .where(DirectUserProfile.user_id == user.id, DirectUserProfile.status == "active")
        )
        or 0
    )
    subscription = latest_user_subscription(user, now)
    key = latest_user_key(user, now)
    active_key = key is not None and key.active and (key.expires_at is None or key.expires_at > now)
    expired_keys = sum(
        1
        for item in user.keys
        if item.key_type == "private" and (not item.active or (item.expires_at is not None and item.expires_at <= now))
    )
    paid_orders = sum(1 for order in user.orders if order.status == "paid")
    total_orders = len(user.orders)
    inbound_count = len(key.x3ui_inbound_ids or []) if key is not None else 0
    plan_kind = plan_kind_for_subscription(subscription, now)
    key_state = "active" if active_key else "expired" if key is not None else "no_key"
    feedback_count = int(
        await session.scalar(
            select(func.count()).select_from(UserFeedback).where(UserFeedback.user_id == user.id)
        )
        or 0
    )
    latest_feedback = await session.scalar(
        select(UserFeedback).where(UserFeedback.user_id == user.id).order_by(UserFeedback.created_at.desc()).limit(1)
    )
    return AdminUserMonitorRow(
        user=user,
        subscription=subscription,
        key=key,
        plan_kind=plan_kind,
        key_state=key_state,
        server_label=key.server_label if key and key.server_label else "нет ключа",
        direct_profiles=direct_profiles,
        assigned_inbounds=inbound_count or direct_profiles,
        expired_keys=expired_keys,
        paid_orders=paid_orders,
        total_orders=total_orders,
        feedback_count=feedback_count,
        last_feedback_rating=latest_feedback.rating if latest_feedback and latest_feedback.rating else "",
        last_feedback_at=latest_feedback.created_at if latest_feedback else None,
    )


async def admin_feedback_rows(session: AsyncSession) -> list[UserFeedback]:
    return list(
        (
            await session.scalars(
                select(UserFeedback)
                .options(selectinload(UserFeedback.user), selectinload(UserFeedback.subscription))
                .order_by(UserFeedback.created_at.desc(), UserFeedback.id.desc())
                .limit(500)
            )
        ).all()
    )


async def user_feedback_rows(session: AsyncSession, user_id: int) -> list[UserFeedback]:
    return list(
        (
            await session.scalars(
                select(UserFeedback)
                .where(UserFeedback.user_id == user_id)
                .order_by(UserFeedback.created_at.desc(), UserFeedback.id.desc())
                .limit(100)
            )
        ).all()
    )


def filter_admin_feedback_rows(feedbacks: list[UserFeedback], params) -> list[UserFeedback]:
    query = str(params.get("q") or "").strip().lower()
    rating = str(params.get("rating") or "all")
    filtered = feedbacks
    if query:
        matched = []
        for feedback in filtered:
            user = feedback.user
            haystack = " ".join(
                [
                    str(user.telegram_id) if user else "",
                    str(user.id) if user else "",
                    (user.username or "") if user else "",
                    (user.first_name or "") if user else "",
                    feedback.text or "",
                    feedback.rating or "",
                ]
            ).lower()
            if query in haystack:
                matched.append(feedback)
        filtered = matched
    if rating != "all":
        filtered = [feedback for feedback in filtered if (feedback.rating or "") == rating]
    return filtered


async def user_direct_profiles(session: AsyncSession, user_id: int) -> list[DirectUserProfile]:
    return list(
        (
            await session.scalars(
                select(DirectUserProfile)
                .where(DirectUserProfile.user_id == user_id)
                .order_by(DirectUserProfile.updated_at.desc(), DirectUserProfile.id.desc())
            )
        ).all()
    )


async def user_activity_snapshots(
    session: AsyncSession,
    user_id: int,
    *,
    days: int = ACTIVITY_SNAPSHOT_DAYS,
) -> list[UserKeyActivitySnapshot]:
    since = utcnow() - timedelta(days=days)
    return list(
        (
            await session.scalars(
                select(UserKeyActivitySnapshot)
                .where(UserKeyActivitySnapshot.user_id == user_id, UserKeyActivitySnapshot.sampled_at >= since)
                .order_by(UserKeyActivitySnapshot.sampled_at.asc())
            )
        ).all()
    )


async def refresh_user_activity_snapshot(session: AsyncSession, key: VpnKey | None) -> ActivityRefreshResult:
    if key is None or key.user_id is None or key.key_type != "private":
        return ActivityRefreshResult(snapshot=None, created=False)

    now = utcnow()
    latest = await session.scalar(
        select(UserKeyActivitySnapshot)
        .where(UserKeyActivitySnapshot.user_id == key.user_id, UserKeyActivitySnapshot.key_id == key.id)
        .order_by(UserKeyActivitySnapshot.sampled_at.desc())
        .limit(1)
    )
    if latest is not None and latest.sampled_at >= now - timedelta(seconds=ACTIVITY_SNAPSHOT_THROTTLE_SECONDS):
        return ActivityRefreshResult(snapshot=latest, created=False, error=latest.error or "")

    try:
        activity = await collect_remote_key_activity(session, key)
        snapshot = UserKeyActivitySnapshot(
            user_id=key.user_id,
            key_id=key.id,
            node_id=activity["node_id"],
            sampled_at=now,
            online=bool(activity["online"]),
            up_bytes=int(activity["up_bytes"]),
            down_bytes=int(activity["down_bytes"]),
            inbound_count=int(activity["inbound_count"]),
            active_inbounds=int(activity["active_inbounds"]),
            matched_clients=int(activity["matched_clients"]),
            source="detail",
            error=None,
        )
    except Exception as exc:
        snapshot = UserKeyActivitySnapshot(
            user_id=key.user_id,
            key_id=key.id,
            node_id=await key_node_id_for_activity(session, key),
            sampled_at=now,
            online=False,
            up_bytes=0,
            down_bytes=0,
            inbound_count=len(key.x3ui_inbound_ids or []),
            active_inbounds=0,
            matched_clients=0,
            source="detail_error",
            error=str(exc)[:500],
        )

    session.add(snapshot)
    await session.flush()
    return ActivityRefreshResult(snapshot=snapshot, created=True, error=snapshot.error or "")


async def collect_remote_key_activity(session: AsyncSession, key: VpnKey) -> dict[str, object]:
    inbound_ids = tuple(dict.fromkeys(item for item in (int_value(raw) for raw in (key.x3ui_inbound_ids or [])) if item > 0))
    if is_direct_vpn_key(key):
        config = await direct_node_config_for_key(session, key)
        if config is None:
            raise RuntimeError("Direct-node config for this key was not found")
        x3ui = x3ui_client_for_config(config)
        node_id = config.node_id
    else:
        x3ui = X3UIClient()
        node_id = "local"

    inbounds = await x3ui.get_inbounds_by_ids(inbound_ids) if inbound_ids else await x3ui.list_inbounds()
    activity = extract_key_activity_from_inbounds(inbounds, key, inbound_ids)
    activity["node_id"] = node_id
    return activity


async def key_node_id_for_activity(session: AsyncSession, key: VpnKey) -> str:
    if is_direct_vpn_key(key):
        config = await direct_node_config_for_key(session, key)
        if config is not None:
            return config.node_id
    return "local"


def extract_key_activity_from_inbounds(
    inbounds: list[dict[str, Any]],
    key: VpnKey,
    inbound_ids: tuple[int, ...],
) -> dict[str, object]:
    wanted_ids = set(inbound_ids)
    now = utcnow()
    inbound_count = 0
    active_inbounds = 0
    matched_clients = 0
    up_bytes = 0
    down_bytes = 0
    online = False

    for inbound in inbounds:
        inbound_id = int_value(inbound.get("id"))
        if wanted_ids and inbound_id not in wanted_ids:
            continue

        client_settings = find_key_client_payload(X3UIClient._inbound_clients(inbound), key)
        client_stat = find_key_client_payload(client_stats_from_inbound(inbound), key)
        if client_settings is None and client_stat is None:
            continue

        inbound_count += 1
        matched_clients += 1
        if client_is_enabled(client_settings, key, now):
            active_inbounds += 1
        if client_stat is not None:
            up_bytes += int_value(client_stat.get("up"))
            down_bytes += int_value(client_stat.get("down"))
            online = online or client_stat_is_online(client_stat)

    if not wanted_ids and matched_clients == 0:
        inbound_count = 0

    return {
        "online": online,
        "up_bytes": up_bytes,
        "down_bytes": down_bytes,
        "inbound_count": inbound_count,
        "active_inbounds": active_inbounds,
        "matched_clients": matched_clients,
    }


def client_stats_from_inbound(inbound: dict[str, Any]) -> list[dict[str, Any]]:
    stats = inbound.get("clientStats")
    return [item for item in stats if isinstance(item, dict)] if isinstance(stats, list) else []


def find_key_client_payload(payloads: list[dict[str, Any]], key: VpnKey) -> dict[str, Any] | None:
    for payload in payloads:
        if payload_matches_key(payload, key):
            return payload
    return None


def payload_matches_key(payload: dict[str, Any], key: VpnKey) -> bool:
    email = str_value(payload.get("email") or payload.get("clientEmail") or payload.get("remark"))
    if email and email == key.email.lower():
        return True
    uuid = str_value(payload.get("id") or payload.get("uuid") or payload.get("clientId") or payload.get("clientUuid"))
    if uuid and uuid == key.x3ui_client_uuid.lower():
        return True
    sub_id = str_value(payload.get("subId") or payload.get("sub") or payload.get("subscriptionId"))
    return bool(sub_id and key.x3ui_sub_id and sub_id == key.x3ui_sub_id.lower())


def client_is_enabled(client_settings: dict[str, Any] | None, key: VpnKey, now) -> bool:
    if client_settings is not None and client_settings.get("enable") is False:
        return False
    expiry_ms = int_value((client_settings or {}).get("expiryTime"))
    if expiry_ms > 0 and expiry_ms <= int(now.timestamp() * 1000):
        return False
    if key.expires_at is not None and key.expires_at <= now:
        return False
    return key.active


def client_stat_is_online(client_stat: dict[str, Any]) -> bool:
    for key in ("online", "isOnline", "is_online"):
        value = client_stat.get(key)
        if isinstance(value, bool):
            return value
        if str_value(value) in {"1", "true", "yes", "online"}:
            return True
    status = str_value(client_stat.get("status") or client_stat.get("state"))
    if status in {"online", "connected", "active", "в сети"}:
        return True

    last_seen = int_value(
        client_stat.get("lastOnline")
        or client_stat.get("lastOnlineTime")
        or client_stat.get("lastSeen")
        or client_stat.get("lastSeenAt")
    )
    if last_seen <= 0:
        return False
    last_seen_seconds = last_seen / 1000 if last_seen > 10_000_000_000 else last_seen
    return utcnow().timestamp() - last_seen_seconds <= 10 * 60


def int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def str_value(value: Any) -> str:
    return str(value or "").strip().lower()


def latest_user_subscription(user: User, now) -> Subscription | None:
    active = [
        subscription
        for subscription in user.subscriptions
        if subscription.status == "active" and subscription.expires_at > now
    ]
    if active:
        return max(active, key=lambda item: item.expires_at)
    return max(user.subscriptions, key=lambda item: item.expires_at, default=None)


def latest_user_key(user: User, now) -> VpnKey | None:
    active = [
        key
        for key in user.keys
        if key.key_type == "private" and key.active and (key.expires_at is None or key.expires_at > now)
    ]
    if active:
        return max(active, key=lambda item: item.created_at)
    private_keys = [key for key in user.keys if key.key_type == "private"]
    return max(private_keys, key=lambda item: item.created_at, default=None)


def plan_kind_for_subscription(subscription: Subscription | None, now) -> str:
    if subscription is None:
        return "none"
    if subscription.status != "active" or subscription.expires_at <= now:
        return "expired"
    if subscription.plan_code == "trial":
        return "free"
    return "paid"


def filter_admin_user_rows(rows: list[AdminUserMonitorRow], params) -> list[AdminUserMonitorRow]:
    query = str(params.get("q") or "").strip().lower()
    plan = str(params.get("plan") or "all")
    key_state = str(params.get("key") or "all")
    server = str(params.get("server") or "all")

    filtered = rows
    if query:
        filtered = [
            row
            for row in filtered
            if query in str(row.user.telegram_id)
            or query in str(row.user.id)
            or query in (row.user.username or "").lower()
            or query in (row.user.first_name or "").lower()
        ]
    if plan != "all":
        filtered = [row for row in filtered if row.plan_kind == plan]
    if key_state != "all":
        filtered = [row for row in filtered if row.key_state == key_state]
    if server != "all":
        filtered = [row for row in filtered if row.server_label == server]
    return filtered


def sort_admin_user_rows(rows: list[AdminUserMonitorRow], sort: str) -> list[AdminUserMonitorRow]:
    sorters = {
        "buy_desc": lambda row: (int(row.user.buy_clicks or 0), row.user.created_at),
        "support_desc": lambda row: (int(row.user.support_clicks or 0), row.user.created_at),
        "replace_desc": lambda row: (int(row.user.replace_key_clicks or 0), row.user.created_at),
        "expires_asc": lambda row: (row.subscription.expires_at if row.subscription else utcnow(), row.user.created_at),
        "inbounds_desc": lambda row: (row.assigned_inbounds, row.user.created_at),
        "paid_orders_desc": lambda row: (row.paid_orders, row.user.created_at),
        "created_desc": lambda row: (row.user.created_at,),
    }
    key = sorters.get(sort, sorters["created_desc"])
    reverse = sort != "expires_asc"
    return sorted(rows, key=key, reverse=reverse)


def paginate_admin_user_rows(rows: list[AdminUserMonitorRow], params) -> tuple[list[AdminUserMonitorRow], int, int]:
    per_page = parse_page_size(str(params.get("per_page") or "50"))
    total_pages = max(1, (len(rows) + per_page - 1) // per_page)
    page = parse_positive_int(str(params.get("page") or "1"), 1)
    page = min(max(1, page), total_pages)
    start = (page - 1) * per_page
    return rows[start : start + per_page], page, per_page


def parse_page_size(value: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 50
    return parsed if parsed in {25, 50, 100} else 50


async def delete_user_access(session: AsyncSession, user_id: int, *, delete_inbounds: bool) -> dict[str, int]:
    now = utcnow()
    keys = (
        await session.scalars(
            select(VpnKey)
            .where(VpnKey.user_id == user_id, VpnKey.key_type == "private", VpnKey.active.is_(True))
            .with_for_update()
        )
    ).all()
    revoked_keys = 0
    deleted_inbounds = 0
    for key in keys:
        await revoke_key_remote(session, key)
        deleted_inbounds += len(key.x3ui_inbound_ids or []) if delete_inbounds else 0
        key.active = False
        key.revoked_at = now
        await release_direct_key_slots(
            session,
            key,
            status="admin_deleted",
            release_slots=delete_inbounds,
            delete_remote_inbounds=delete_inbounds,
        )
        revoked_keys += 1

    subscriptions = (
        await session.scalars(
            select(Subscription)
            .where(Subscription.user_id == user_id, Subscription.status == "active")
            .with_for_update()
        )
    ).all()
    for subscription in subscriptions:
        subscription.status = "admin_deleted"

    return {"keys": revoked_keys, "inbounds": deleted_inbounds}


async def get_admin_user(session: AsyncSession, telegram_id: int) -> User | None:
    if not await is_admin_telegram_id(session, telegram_id):
        return None
    return await session.scalar(select(User).where(User.telegram_id == telegram_id))


async def get_or_create_admin_user(session: AsyncSession, telegram_id: int) -> User:
    if not await is_admin_telegram_id(session, telegram_id):
        raise HTTPException(status_code=403, detail="Telegram ID is not an admin")

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

    existing = await session.scalar(select(BotAdmin).where(BotAdmin.telegram_id == telegram_id))
    if existing is None:
        session.add(BotAdmin(telegram_id=telegram_id, added_at=utcnow()))

    await session.flush()
    return user


async def is_admin_telegram_id(session: AsyncSession, telegram_id: int) -> bool:
    settings = get_settings()
    if telegram_id in settings.admin_ids:
        return True
    admin = await session.scalar(select(BotAdmin).where(BotAdmin.telegram_id == telegram_id))
    return admin is not None


async def active_subscriptions(session: AsyncSession) -> list[tuple[Subscription, VpnKey | None]]:
    subscriptions = (
        await session.scalars(
            select(Subscription)
            .options(
                selectinload(Subscription.user),
                selectinload(Subscription.plan),
                selectinload(Subscription.keys),
            )
            .where(Subscription.status == "active", Subscription.expires_at > utcnow())
            .order_by(Subscription.expires_at.desc())
        )
    ).all()

    rows: list[tuple[Subscription, VpnKey | None]] = []
    for subscription in subscriptions:
        key = next((item for item in subscription.keys if item.active and item.key_type == "private"), None)
        rows.append((subscription, key))
    return rows


async def latest_pending_orders(session: AsyncSession) -> list[Order]:
    return list(
        (
            await session.scalars(
                select(Order)
                .options(selectinload(Order.user), selectinload(Order.plan))
                .where(Order.status == "pending", Order.expires_at > utcnow())
                .order_by(Order.created_at.desc())
                .limit(10)
            )
        ).all()
    )


def redirect_to_admin(request: Request) -> RedirectResponse:
    token = request.query_params.get("token")
    suffix = f"?{urlencode({'token': token})}" if token else ""
    return RedirectResponse(url=f"/admin{suffix}", status_code=303)


def redirect_to_admin_users(token: str, notice: str) -> RedirectResponse:
    query = {"notice": notice}
    if token:
        query["token"] = token
    return RedirectResponse(url=f"/admin/users?{urlencode(query)}", status_code=303)


def redirect_to_admin_user_detail(user_id: int, token: str, notice: str) -> RedirectResponse:
    query = {"notice": notice}
    if token:
        query["token"] = token
    return RedirectResponse(url=f"/admin/users/{user_id}?{urlencode(query)}", status_code=303)


def serialize_subscription(subscription: Subscription, key: VpnKey | None) -> dict[str, object]:
    user = subscription.user
    return {
        "id": subscription.id,
        "telegram_id": user.telegram_id,
        "username": user.username,
        "plan": subscription.plan_code,
        "expires_at": subscription.expires_at.isoformat(),
        "key_active": key.active if key else False,
    }


def render_admin_profile_login_page(request: Request, error: str | None = None) -> str:
    settings = get_settings()
    bot_username = settings.bot_username.strip().lstrip("@")
    auth_url = escape(str(request.url_for("admin_profile_login")))
    error_html = f'<p class="error">{escape(error)}</p>' if error else ""
    if bot_username:
        login = (
            f'<script async src="https://telegram.org/js/telegram-widget.js?22" '
            f'data-telegram-login="{escape(bot_username)}" '
            'data-size="large" '
            f'data-auth-url="{auth_url}" '
            'data-request-access="write"></script>'
        )
    else:
        login = '<p class="muted">BOT_USERNAME не задан. Открой профиль через кнопку в Telegram-боте.</p>'

    return f"""<!doctype html>
<html lang="ru">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>MiloshVPN Admin Profile</title>
    <style>{profile_page_css()}</style>
  </head>
  <body>
    <main class="profile-shell">
      <section class="profile-panel">
        <p class="eyebrow">MiloshVPN</p>
        <h1>Вход администратора</h1>
        {error_html}
        <div class="login-box">{login}</div>
      </section>
    </main>
  </body>
</html>"""


def render_admin_profile_page(telegram_id: int, user: User | None, *, auth_source: str) -> str:
    username = f"@{escape(user.username)}" if user and user.username else "не указан"
    first_name = escape(user.first_name) if user and user.first_name else "не указано"
    role = escape(user.role) if user else "admin"
    panel_url = escape(admin_panel_url_with_token())
    return f"""<!doctype html>
<html lang="ru">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>MiloshVPN Admin Profile</title>
    <style>{profile_page_css()}</style>
  </head>
  <body>
    <main class="profile-shell">
      <section class="profile-panel">
        <p class="eyebrow">MiloshVPN</p>
        <h1>Профиль администратора</h1>
        <dl>
          <div><dt>Telegram ID</dt><dd><code>{telegram_id}</code></dd></div>
          <div><dt>Username</dt><dd>{username}</dd></div>
          <div><dt>Имя</dt><dd>{first_name}</dd></div>
          <div><dt>Роль</dt><dd>{role}</dd></div>
          <div><dt>Вход</dt><dd>{escape(auth_source)}</dd></div>
        </dl>
        <a class="button" href="{panel_url}">Открыть админку</a>
      </section>
    </main>
  </body>
</html>"""


def profile_page_css() -> str:
    return """
      :root {
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        color: #15191f;
        background: #eef2f6;
      }
      body {
        margin: 0;
      }
      .profile-shell {
        min-height: 100vh;
        display: grid;
        place-items: center;
        padding: 24px;
      }
      .profile-panel {
        width: min(100%, 460px);
        background: #fff;
        border: 1px solid #dbe2ea;
        border-radius: 8px;
        padding: 24px;
        box-shadow: 0 16px 44px rgba(20, 28, 38, 0.09);
      }
      .eyebrow {
        margin: 0 0 8px;
        color: #576273;
        font-size: 13px;
      }
      h1 {
        margin: 0 0 20px;
        font-size: 24px;
        letter-spacing: 0;
      }
      dl {
        display: grid;
        gap: 12px;
        margin: 0 0 22px;
      }
      dl div {
        display: grid;
        grid-template-columns: 120px 1fr;
        gap: 12px;
        align-items: baseline;
      }
      dt {
        color: #657186;
        font-size: 13px;
      }
      dd {
        margin: 0;
        min-width: 0;
        word-break: break-word;
      }
      code {
        word-break: break-all;
      }
      .button {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-height: 40px;
        padding: 0 14px;
        border-radius: 6px;
        background: #1563ff;
        color: #fff;
        text-decoration: none;
      }
      .login-box {
        min-height: 48px;
        display: flex;
        align-items: center;
      }
      .muted {
        margin: 0;
        color: #657186;
      }
      .error {
        margin: 0 0 16px;
        color: #a51d2d;
      }
      @media (max-width: 520px) {
        .profile-panel {
          padding: 18px;
        }
        dl div {
          grid-template-columns: 1fr;
          gap: 4px;
        }
      }
    """


def admin_nav(token: str, active: str) -> str:
    token_qs = f"?{urlencode({'token': token})}" if token else ""
    items = [
        ("dashboard", "Главная", f"/admin{token_qs}"),
        ("users", "Пользователи", f"/admin/users{token_qs}"),
        ("feedback", "Отзывы", f"/admin/feedback{token_qs}"),
        ("direct_nodes", "Direct nodes", f"/admin/direct-nodes{token_qs}"),
    ]
    links = "\n".join(
        f'<a class="{"active" if code == active else ""}" href="{escape(url)}">{escape(label)}</a>'
        for code, label, url in items
    )
    return f'<nav class="admin-tabs" aria-label="Admin navigation">{links}</nav>'


def admin_nav_css() -> str:
    return """
      .admin-tabs {
        display: flex;
        gap: 8px;
        flex-wrap: wrap;
        margin-top: 16px;
      }
      .admin-tabs a {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-height: 38px;
        padding: 0 12px;
        border: 1px solid #cad2dc;
        border-radius: 6px;
        background: #fff;
        color: #121417;
        text-decoration: none;
        font-size: 14px;
      }
      .admin-tabs a.active {
        border-color: #1563ff;
        background: #1563ff;
        color: #fff;
      }
      header .admin-tabs a {
        border-color: #3a424f;
        background: #20252e;
        color: #fff;
      }
      header .admin-tabs a.active {
        border-color: #fff;
        background: #fff;
        color: #121417;
      }
      @media (max-width: 560px) {
        .admin-tabs {
          gap: 6px;
        }
        .admin-tabs a {
          flex: 1 1 calc(50% - 6px);
          padding: 0 10px;
        }
      }
    """


def render_admin_page(
    stats: dict[str, int],
    system_state: dict[str, object],
    subscriptions: list[tuple[Subscription, VpnKey | None]],
    pending_orders: list[Order],
    private_keys: list[VpnKey],
    token: str,
    payment_provider: str,
) -> str:
    settings = get_settings()
    token_qs = f"?{urlencode({'token': token})}" if token else ""
    token_input = hidden_token_input(token)
    default_admin_id = str(settings.admin_ids[0]) if settings.admin_ids else ""
    system_status = render_system_x3ui_status(system_state)
    sub_rows = "\n".join(render_subscription_row(subscription, key) for subscription, key in subscriptions) or table_empty(
        "Активных подписок пока нет"
    )
    key_rows = "\n".join(render_private_key_row(key, token_qs) for key in private_keys) or table_empty(
        "Личных активных ключей пока нет"
    )
    order_rows = "\n".join(render_order_row(order, token_qs) for order in pending_orders) or table_empty("Ожидающих оплат нет")
    payment_provider_buttons = "\n".join(
        render_payment_provider_button(provider.code, provider.label, payment_provider, token_qs)
        for provider in list_payment_providers()
    )
    return f"""<!doctype html>
<html lang="ru">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>MiloshVPN Admin</title>
    <style>
      :root {{
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        color: #121417;
        background: #f5f7fa;
      }}
      body {{
        margin: 0;
      }}
      header {{
        background: #121417;
        color: #fff;
        padding: 22px 28px;
      }}
      main {{
        max-width: 1180px;
        margin: 0 auto;
        padding: 24px;
      }}
      h1, h2 {{
        margin: 0;
        letter-spacing: 0;
      }}
      h1 {{
        font-size: 28px;
      }}
      h2 {{
        font-size: 20px;
        margin-bottom: 14px;
      }}
      section {{
        margin: 0 0 26px;
      }}
      .stats {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
        gap: 12px;
      }}
      .stat {{
        background: #fff;
        border: 1px solid #dfe5ec;
        border-radius: 8px;
        padding: 14px;
      }}
      .stat span {{
        display: block;
        color: #687385;
        font-size: 13px;
      }}
      .stat strong {{
        display: block;
        margin-top: 6px;
        font-size: 26px;
      }}
      .panel {{
        background: #fff;
        border: 1px solid #dfe5ec;
        border-radius: 8px;
        padding: 18px;
      }}
      table {{
        width: 100%;
        border-collapse: collapse;
        font-size: 14px;
      }}
      th, td {{
        text-align: left;
        border-bottom: 1px solid #e6ebf1;
        padding: 10px 8px;
        vertical-align: top;
      }}
      th {{
        color: #566174;
        font-size: 12px;
        text-transform: uppercase;
      }}
      form.grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
        gap: 12px;
      }}
      label {{
        display: grid;
        gap: 6px;
        color: #566174;
        font-size: 13px;
      }}
      input, select {{
        box-sizing: border-box;
        width: 100%;
        border: 1px solid #cad2dc;
        border-radius: 6px;
        padding: 9px 10px;
        font: inherit;
        background: #fff;
      }}
      button {{
        border: 0;
        border-radius: 6px;
        padding: 9px 12px;
        font: inherit;
        cursor: pointer;
        background: #1563ff;
        color: #fff;
      }}
      button.secondary {{
        background: #e9eef5;
        color: #121417;
      }}
      button.danger {{
        background: #d93636;
      }}
      .actions {{
        display: flex;
        gap: 8px;
        align-items: center;
        flex-wrap: wrap;
      }}
      .badge {{
        display: inline-block;
        border-radius: 999px;
        padding: 3px 8px;
        background: #e9eef5;
        color: #273244;
        font-size: 12px;
      }}
      .badge.active {{
        background: #dff7e8;
        color: #116b35;
      }}
      .badge.offline {{
        background: #ffe3e3;
        color: #9f1d1d;
      }}
      .muted {{
        color: #687385;
        font-size: 12px;
      }}
      .error {{
        color: #a51d2d;
      }}
      code {{
        word-break: break-all;
      }}
      {admin_nav_css()}
    </style>
  </head>
  <body>
    <header>
      <h1>MiloshVPN Admin</h1>
      {admin_nav(token, "dashboard")}
    </header>
    <main>
      <section class="stats">
        {render_stat("Пользователи", stats["users"])}
        {render_stat("Активные подписки", stats["active_subscriptions"])}
        {render_stat("Оплаченные заказы", stats["paid_orders"])}
        {render_stat("Ожидают оплаты", stats["pending_orders"])}
        {render_stat("Активные ключи", stats["active_keys"])}
      </section>

      <section class="panel">
        <h2>Система оплаты</h2>
        <p class="muted" style="margin-top: 0;">Сейчас активна: <strong>{escape(payment_provider_label(payment_provider))}</strong></p>
        <div class="actions">{payment_provider_buttons}</div>
      </section>

      <section class="panel">
        <h2>Админские ключи</h2>
        <form method="post" action="/admin/admin-keys{token_qs}" class="grid">
          <label>Telegram ID админа<input name="telegram_id" value="{escape(default_admin_id)}" inputmode="numeric"></label>
          <div class="actions"><button type="submit">Создать admin key</button></div>
        </form>
        <form method="post" action="/admin/admin-reality-keys{token_qs}" class="grid" style="margin-top: 12px;">
          <label>Telegram ID админа<input name="telegram_id" value="{escape(default_admin_id)}" inputmode="numeric"></label>
          <div class="actions"><button class="secondary" type="submit">Создать Reality test key</button></div>
        </form>
        <p class="muted" style="margin-top: 10px;">Ключ создаётся без оплаты и привязывается к Telegram ID админа.</p>
      </section>

      <section class="panel">
        <h2>Найти оплату и выдать ключ</h2>
        <form method="get" action="/admin/orders/search" class="grid">
          {token_input}
          <label>Код, Telegram ID или ID заказа<input name="query" placeholder="MILO-805074848-55481D"></label>
          <div class="actions"><button type="submit">Найти</button></div>
        </form>
        <p class="muted" style="margin-top: 10px;">После поиска можно вручную отметить заказ оплаченным и отправить ключ пользователю.</p>
      </section>

      <section class="panel">
        <h2>Системная 3x-ui</h2>
        {system_status}
      </section>

      <section class="panel">
        <h2>Direct-node тест</h2>
        <p class="muted" style="margin-top: 0;">Отдельная настройка немецких direct-node узлов для админского теста. Основная система 3x-ui не меняется.</p>
        <div class="actions"><a class="button secondary" href="/admin/direct-nodes{token_qs}">Открыть direct nodes</a></div>
      </section>

      <section class="panel">
        <h2>Пользователи</h2>
        <p class="muted" style="margin-top: 0;">Мониторинг подписок, inbound-ов, серверов и кликов по Купить / Поддержка / Заменить sub.</p>
        <div class="actions"><a class="button secondary" href="/admin/users{token_qs}">Открыть пользователей</a></div>
      </section>

      <section class="panel">
        <h2>Активные подписки</h2>
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Пользователь</th>
              <th>Тариф</th>
              <th>До</th>
              <th>Ключ</th>
            </tr>
          </thead>
          <tbody>{sub_rows}</tbody>
        </table>
      </section>

      <section class="panel">
        <h2>Личные ключи</h2>
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Пользователь</th>
              <th>Тариф</th>
              <th>До</th>
              <th>Label</th>
              <th></th>
            </tr>
          </thead>
          <tbody>{key_rows}</tbody>
        </table>
      </section>

      <section class="panel">
        <h2>Ожидающие оплаты</h2>
        <div class="actions" style="margin-bottom: 12px;">
          <form method="post" action="/admin/donations/poll{token_qs}"><button type="submit">Проверить DonationAlerts</button></form>
          <form method="post" action="/admin/public-key/rotate{token_qs}"><button class="secondary" type="submit">Пересоздать free key</button></form>
          <form method="post" action="/admin/subscriptions/cleanup-expired{token_qs}"><button class="secondary" type="submit">Очистить истёкшие</button></form>
        </div>
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Пользователь</th>
              <th>Тариф</th>
              <th>Сумма</th>
              <th>Код</th>
              <th>Истекает</th>
              <th></th>
            </tr>
          </thead>
          <tbody>{order_rows}</tbody>
        </table>
      </section>
    </main>
  </body>
</html>"""


def render_admin_key_page(key: VpnKey, telegram_id: int, token: str, *, reality: bool = False) -> str:
    token_qs = f"?{urlencode({'token': token})}" if token else ""
    heading = "Админский Reality test key создан" if reality else "Админский ключ создан"
    key_label = "Reality sub-ссылка" if reality else "Sub-ссылка"
    return f"""<!doctype html>
<html lang="ru">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Admin key</title>
    <style>{profile_page_css()}</style>
  </head>
  <body>
    <main class="profile-shell">
      <section class="profile-panel">
        <p class="eyebrow">MiloshVPN</p>
        <h1>{heading}</h1>
        <dl>
          <div><dt>Telegram ID</dt><dd><code>{telegram_id}</code></dd></div>
          <div><dt>Label</dt><dd><code>{escape(key.email)}</code></dd></div>
        </dl>
        <p class="muted">{key_label}</p>
        <code>{escape(key.vless_uri)}</code>
        <div class="actions" style="margin-top: 18px;">
          <a class="button" href="/admin{token_qs}">Вернуться в админку</a>
        </div>
      </section>
    </main>
  </body>
</html>"""


def render_order_search_page(query: str, orders: list[Order], token: str) -> str:
    token_qs = f"?{urlencode({'token': token})}" if token else ""
    token_input = hidden_token_input(token)
    rows = "\n".join(render_order_search_row(order, token_qs) for order in orders) if query else ""
    empty = ""
    if query and not orders:
        empty = '<p class="muted">Ничего не найдено. Проверь код или Telegram ID.</p>'
    table = ""
    if rows:
        table = f"""
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Пользователь</th>
              <th>Тариф</th>
              <th>Сумма</th>
              <th>Статус</th>
              <th>Код</th>
              <th></th>
            </tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>"""
    return f"""<!doctype html>
<html lang="ru">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Поиск оплаты</title>
    <style>{admin_simple_page_css()}</style>
  </head>
  <body>
    <main>
      <section class="panel">
        <p class="eyebrow">MiloshVPN Admin</p>
        <h1>Найти оплату</h1>
        <form method="get" action="/admin/orders/search" class="grid">
          {token_input}
          <label>Код, Telegram ID или ID заказа<input name="query" value="{escape(query)}" placeholder="MILO-805074848-55481D"></label>
          <div class="actions"><button type="submit">Найти</button><a class="button secondary" href="/admin{token_qs}">Назад</a></div>
        </form>
      </section>
      <section class="panel">
        <h2>Результат</h2>
        {empty}
        {table}
      </section>
    </main>
  </body>
</html>"""


def render_admin_users_page(
    rows: list[AdminUserMonitorRow],
    all_rows: list[AdminUserMonitorRow],
    filtered_count: int,
    page: int,
    per_page: int,
    nodes: list[DirectNodeConfig],
    plans: list[Plan],
    request: Request,
    token: str,
) -> str:
    token_qs = f"?{urlencode({'token': token})}" if token else ""
    notice = str(request.query_params.get("notice") or "")
    notice_html = f'<p class="notice">{escape(notice)}</p>' if notice else ""
    filters = render_admin_user_filters(request, token, all_rows)
    stats = render_admin_user_stats(all_rows)
    pagination = render_admin_user_pagination(request, token, filtered_count, page, per_page)
    node_options = direct_node_issue_options(nodes)
    plan_options = admin_issue_plan_options(plans)
    table_rows = "\n".join(render_admin_user_row(row, token_qs) for row in rows) or table_empty("По фильтрам никого нет")
    return f"""<!doctype html>
<html lang="ru">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Пользователи MiloshVPN</title>
    <style>{admin_users_page_css()}</style>
  </head>
  <body>
    <main>
      <section class="panel">
        <p class="eyebrow">MiloshVPN Admin</p>
        <h1>Пользователи</h1>
        <p class="muted">Все пользователи, подписки, ключи, direct inbound-ы и действия в боте.</p>
        {admin_nav(token, "users")}
      </section>

      <section class="stats">{stats}</section>

      <section class="panel">
        <h2>Фильтры</h2>
        {notice_html}
        {filters}
      </section>

      <section class="panel">
        <div class="list-head">
          <h2>Список: {len(rows)} на странице, найдено {filtered_count} из {len(all_rows)}</h2>
          {pagination}
        </div>
        <table class="users-table">
          <thead>
            <tr>
              <th>Пользователь</th>
              <th>Подписка</th>
              <th>Ключ / сервер</th>
              <th>Inbound-ы</th>
              <th>Действия</th>
              <th></th>
            </tr>
          </thead>
          <tbody>{table_rows}</tbody>
        </table>
        {pagination}
      </section>

      <dialog id="delete-dialog">
        <form method="post" id="delete-form">
          <h2>Удалить доступ?</h2>
          <p id="delete-user-label" class="muted"></p>
          <label class="checkline">
            <input type="checkbox" name="delete_inbounds" value="1">
            Удалить также direct inbound-ы пользователя
          </label>
          <p class="muted">Без галочки будет удалён только активный клиент/ключ, а inbound-слоты останутся закреплены за пользователем.</p>
          <div class="actions">
            <button class="danger" type="submit">Удалить</button>
            <button class="secondary" type="button" onclick="closeDeleteDialog()">Отмена</button>
          </div>
        </form>
      </dialog>

      <dialog id="issue-key-dialog">
        <form method="post" id="issue-key-form">
          <h2>Выдать ключ?</h2>
          <p id="issue-key-user-label" class="muted"></p>
          <label>Узел / страна<select name="node_id">{node_options}</select></label>
          <label>Тариф<select name="plan_code">{plan_options}</select></label>
          <p class="muted">Можно оставить текущую подписку или сразу назначить выбранный тариф. Бот создаст новый sub и отправит его в Telegram.</p>
          <div class="actions">
            <button type="submit">Да, выдать</button>
            <button class="secondary" type="button" onclick="closeIssueKeyDialog()">Нет</button>
          </div>
        </form>
      </dialog>
    </main>
    <script>
      const dialog = document.getElementById('delete-dialog');
      const form = document.getElementById('delete-form');
      const label = document.getElementById('delete-user-label');
      const issueDialog = document.getElementById('issue-key-dialog');
      const issueForm = document.getElementById('issue-key-form');
      const issueLabel = document.getElementById('issue-key-user-label');
      function openDeleteDialog(button) {{
        form.action = button.dataset.action;
        label.textContent = button.dataset.user;
        form.querySelector('input[name="delete_inbounds"]').checked = false;
        if (dialog.showModal) dialog.showModal(); else if (confirm('Удалить доступ пользователя?')) form.submit();
      }}
      function closeDeleteDialog() {{
        dialog.close();
      }}
      function openIssueKeyDialog(button) {{
        issueForm.action = button.dataset.action;
        issueLabel.textContent = button.dataset.user;
        if (issueDialog.showModal) issueDialog.showModal(); else if (confirm('Выдать ключ пользователю?')) issueForm.submit();
      }}
      function closeIssueKeyDialog() {{
        issueDialog.close();
      }}
    </script>
  </body>
</html>"""


def render_admin_feedback_page(
    feedbacks: list[UserFeedback],
    all_feedbacks: list[UserFeedback],
    request: Request,
    token: str,
) -> str:
    rows = "\n".join(render_admin_feedback_row(feedback, token) for feedback in feedbacks) or table_empty("Отзывов по фильтру нет")
    filters = render_admin_feedback_filters(request, token)
    stats = render_admin_feedback_stats(all_feedbacks)
    return f"""<!doctype html>
<html lang="ru">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Отзывы MiloshVPN</title>
    <style>{admin_users_page_css()}</style>
  </head>
  <body>
    <main>
      <section class="panel">
        <p class="eyebrow">MiloshVPN Admin</p>
        <h1>Отзывы</h1>
        <p class="muted">Оценки и свободные сообщения пользователей из trial-опросов.</p>
        {admin_nav(token, "feedback")}
      </section>

      <section class="stats">{stats}</section>

      <section class="panel">
        <h2>Фильтры</h2>
        {filters}
      </section>

      <section class="panel">
        <div class="list-head">
          <h2>Найдено: {len(feedbacks)} из {len(all_feedbacks)}</h2>
        </div>
        <table class="users-table">
          <thead>
            <tr>
              <th>Дата</th>
              <th>Пользователь</th>
              <th>Оценка</th>
              <th>Отзыв</th>
              <th>Источник</th>
              <th>Админ</th>
            </tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>
      </section>
    </main>
  </body>
</html>"""


def render_admin_feedback_filters(request: Request, token: str) -> str:
    params = request.query_params
    q = str(params.get("q") or "")
    rating = str(params.get("rating") or "all")
    return f"""
        <form method="get" action="/admin/feedback" class="grid filters">
          {hidden_token_input(token)}
          <label class="search-field">Поиск<input name="q" value="{escape(q)}" placeholder="@username, Telegram ID, текст"></label>
          <label>Оценка<select name="rating">{option_tags(feedback_rating_options(), rating)}</select></label>
          <div class="actions">
            <button type="submit">Показать</button>
            <a class="button secondary" href="/admin/feedback{('?' + urlencode({'token': token})) if token else ''}">Сбросить</a>
          </div>
        </form>
    """


def render_admin_feedback_stats(feedbacks: list[UserFeedback]) -> str:
    ratings = Counter(feedback.rating or "empty" for feedback in feedbacks)
    text_count = sum(1 for feedback in feedbacks if feedback.text)
    unsent = sum(1 for feedback in feedbacks if feedback.sent_to_admin_at is None)
    return "\n".join(
        [
            render_stat("Всего", len(feedbacks)),
            render_stat("С текстом", text_count),
            render_stat("Отлично", ratings["excellent"]),
            render_stat("Хорошо", ratings["good"]),
            render_stat("Удовлетворительно", ratings["satisfactory"]),
            render_stat("Не отправлено админу", unsent),
        ]
    )


def render_admin_feedback_row(feedback: UserFeedback, token: str) -> str:
    user = feedback.user
    token_qs = f"?{urlencode({'token': token})}" if token else ""
    username = f"@{escape(user.username)}" if user and user.username else "без username"
    user_link = f"/admin/users/{user.id}{token_qs}" if user else "#"
    user_id = user.telegram_id if user else feedback.user_id
    text = escape(feedback.text or "без текста")
    sent = format_admin_dt(feedback.sent_to_admin_at) if feedback.sent_to_admin_at else "нет"
    return f"""<tr>
      <td data-label="Дата">{format_admin_dt(feedback.created_at)}</td>
      <td data-label="Пользователь"><a class="user-link" href="{escape(user_link)}"><strong>{username}</strong></a><br><code>{user_id}</code></td>
      <td data-label="Оценка"><span class="badge">{escape(feedback_rating_admin_label(feedback.rating))}</span></td>
      <td data-label="Отзыв">{text}</td>
      <td data-label="Источник">{escape(feedback.source)}</td>
      <td data-label="Админ">{sent}</td>
    </tr>"""


def feedback_rating_options() -> list[tuple[str, str]]:
    return [
        ("all", "Все"),
        ("excellent", "отлично"),
        ("good", "хорошо"),
        ("satisfactory", "удовлетворительно"),
        ("custom", "сам напешууууУ"),
    ]


def render_admin_user_stats(rows: list[AdminUserMonitorRow]) -> str:
    total = len(rows)
    active_keys = sum(1 for row in rows if row.key_state == "active")
    paid = sum(1 for row in rows if row.plan_kind == "paid")
    free = sum(1 for row in rows if row.plan_kind == "free")
    expired = sum(1 for row in rows if row.plan_kind == "expired")
    direct_inbounds = sum(row.assigned_inbounds for row in rows)
    server_counts = Counter(row.server_label for row in rows if row.server_label != "нет ключа")
    server_text = ", ".join(f"{escape(server)}: {count}" for server, count in server_counts.most_common()) or "нет"
    return "\n".join(
        [
            render_stat("Всего", total),
            render_stat("Активные ключи", active_keys),
            render_stat("Платные", paid),
            render_stat("Бесплатные", free),
            render_stat("Истекли", expired),
            render_stat("Inbound-ы", direct_inbounds),
            f'<div class="stat wide"><span>По серверам</span><strong>{server_text}</strong></div>',
        ]
    )


def render_admin_user_detail_page(
    row: AdminUserMonitorRow,
    direct_profiles: list[DirectUserProfile],
    nodes: list[DirectNodeConfig],
    plans: list[Plan],
    feedbacks: list[UserFeedback],
    activity_snapshots: list[UserKeyActivitySnapshot],
    activity_result: ActivityRefreshResult,
    request: Request,
    token: str,
) -> str:
    user = row.user
    token_qs = f"?{urlencode({'token': token})}" if token else ""
    notice = str(request.query_params.get("notice") or "")
    notice_html = f'<p class="notice">{escape(notice)}</p>' if notice else ""
    username = f"@{escape(user.username)}" if user.username else "без username"
    local_activity = key_activity_buckets([key for key in user.keys if key.key_type == "private"])
    remote_activity = snapshot_activity_buckets(activity_snapshots)
    remote_hours = sum(float(item["hours"]) for item in remote_activity)
    remote_days = sum(1 for item in remote_activity if float(item["hours"]) > 0)
    local_hours = sum(float(item["hours"]) for item in local_activity)
    local_days = sum(1 for item in local_activity if float(item["hours"]) > 0)
    activity_hours = remote_hours if activity_snapshots else local_hours
    activity_days = remote_days if activity_snapshots else local_days
    activity_days_label = "Активных дней за 30" if activity_snapshots else "Дней с ключом за 30"
    activity_hours_label = "Активных часов-снимков" if activity_snapshots else "Часов с ключом за 30"
    node_options = direct_node_issue_options(nodes)
    plan_options = admin_issue_plan_options(plans)
    issue_key_url = f"/admin/users/{user.id}/issue-key{token_qs}"
    delete_url = f"/admin/users/{user.id}/delete-access{token_qs}"
    subscription_rows = render_detail_subscription_rows(user.subscriptions)
    key_rows = render_detail_key_rows(user.keys)
    order_rows = render_detail_order_rows(user.orders)
    profile_rows = render_detail_profile_rows(direct_profiles)
    feedback_rows = render_detail_feedback_rows(feedbacks)
    return f"""<!doctype html>
<html lang="ru">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Пользователь {user.telegram_id}</title>
    <style>{admin_users_page_css()}</style>
  </head>
  <body>
    <main>
      <section class="panel">
        <p class="eyebrow">MiloshVPN Admin</p>
        <h1>{username}</h1>
        <p class="muted">Telegram ID: <code>{user.telegram_id}</code> · User ID: <code>{user.id}</code> · создан: {format_admin_dt(user.created_at)}</p>
        {admin_nav(token, "users")}
      </section>

      {notice_html}

      <section class="stats">
        {render_stat(activity_days_label, activity_days)}
        {render_stat(activity_hours_label, int(round(activity_hours)))}
        {render_stat("Снимков активности", len(activity_snapshots))}
        {render_stat("Inbound-ы", row.assigned_inbounds)}
        {render_stat("Оплаты", row.paid_orders)}
        {render_stat("Купить", int(user.buy_clicks or 0))}
        {render_stat("Поддержка", int(user.support_clicks or 0))}
        {render_stat("Замены", int(user.replace_key_clicks or 0))}
        {render_stat("Любимая кнопка", favorite_action_text(user))}
      </section>

      <section class="panel">
        <h2>Быстрые действия</h2>
        <div class="actions">
          <button class="secondary icon-button" type="button" title="Выдать ключ" data-action="{escape(issue_key_url)}" data-user="{username} / {user.telegram_id}" onclick="openIssueKeyDialog(this)">🔑</button>
          <button class="danger" type="button" data-action="{escape(delete_url)}" data-user="{username} / {user.telegram_id}" onclick="openDeleteDialog(this)">Удалить доступ</button>
          <a class="button secondary" href="/admin/orders/search{token_qs}{'&' if token_qs else '?'}query={user.telegram_id}">Оплаты</a>
          <a class="button secondary" href="/admin/users{token_qs}">Назад к списку</a>
        </div>
      </section>

      <section class="panel">
        <h2>Live-статистика ключа</h2>
        {render_live_activity_status(activity_result, activity_snapshots)}
      </section>

      <section class="panel">
        <h2>Активность по снимкам 3x-ui</h2>
        <p class="muted">Снимок создаётся при открытии этой карточки и не чаще одного раза в 2 минуты по конкретному ключу. Час считается активным, если 3x-ui вернул online или между снимками вырос трафик.</p>
        {render_key_activity_chart(remote_activity)}
        <h2 class="subheading">Период действия ключа в БД</h2>
        <p class="muted">Этот нижний график механически показывает, когда ключ считался действующим в нашей базе.</p>
        {render_key_activity_chart(local_activity)}
      </section>

      <section class="panel">
        <h2>Клики в боте</h2>
        {render_user_click_details(user)}
      </section>

      <section class="panel">
        <h2>Отзывы</h2>
        <table class="detail-table">{feedback_rows}</table>
      </section>

      <section class="panel">
        <h2>Подписки</h2>
        <table class="detail-table">{subscription_rows}</table>
      </section>

      <section class="panel">
        <h2>Ключи</h2>
        <table class="detail-table">{key_rows}</table>
      </section>

      <section class="panel">
        <h2>Direct-профили</h2>
        <table class="detail-table">{profile_rows}</table>
      </section>

      <section class="panel">
        <h2>Заказы</h2>
        <table class="detail-table">{order_rows}</table>
      </section>

      {render_user_action_dialogs(user, token_qs, node_options, plan_options, username)}
    </main>
    {render_user_action_scripts()}
  </body>
</html>"""


def key_activity_buckets(keys: list[VpnKey], days: int = 30) -> list[dict[str, object]]:
    now = utcnow()
    first_day = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
    buckets: list[dict[str, object]] = []
    for index in range(days):
        day_start = first_day + timedelta(days=index)
        day_end = min(day_start + timedelta(days=1), now)
        hours = 0.0
        active_keys = 0
        for key in keys:
            start = key.created_at
            end = key.revoked_at or key.expires_at or now
            end = min(end, now)
            overlap_start = max(start, day_start)
            overlap_end = min(end, day_end)
            if overlap_end <= overlap_start:
                continue
            hours += (overlap_end - overlap_start).total_seconds() / 3600
            active_keys += 1
        buckets.append({"date": day_start.strftime("%d.%m"), "hours": min(hours, 24.0), "keys": active_keys})
    return buckets


def snapshot_activity_buckets(
    snapshots: list[UserKeyActivitySnapshot],
    days: int = ACTIVITY_SNAPSHOT_DAYS,
) -> list[dict[str, object]]:
    now = utcnow()
    first_day = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
    active_hours: set[object] = set()
    previous: UserKeyActivitySnapshot | None = None
    for snapshot in sorted(snapshots, key=lambda item: item.sampled_at):
        if snapshot.sampled_at < first_day:
            previous = snapshot
            continue
        total_bytes = int(snapshot.up_bytes or 0) + int(snapshot.down_bytes or 0)
        previous_bytes = int(previous.up_bytes or 0) + int(previous.down_bytes or 0) if previous is not None else total_bytes
        if snapshot.online or (previous is not None and total_bytes > previous_bytes):
            active_hours.add(snapshot.sampled_at.replace(minute=0, second=0, microsecond=0))
        previous = snapshot
    buckets: list[dict[str, object]] = []
    for index in range(days):
        day_start = first_day + timedelta(days=index)
        day_end = min(day_start + timedelta(days=1), now)
        hours = sum(1 for sampled_hour in active_hours if day_start <= sampled_hour < day_end)
        samples = sum(1 for snapshot in snapshots if day_start <= snapshot.sampled_at < day_end)
        buckets.append({"date": day_start.strftime("%d.%m"), "hours": float(hours), "samples": samples})
    return buckets


def render_live_activity_status(
    activity_result: ActivityRefreshResult,
    snapshots: list[UserKeyActivitySnapshot],
) -> str:
    snapshot = activity_result.snapshot or (snapshots[-1] if snapshots else None)
    if snapshot is None:
        return '<p class="muted">Нет активного личного ключа для точечной проверки.</p>'

    online_label = "online" if snapshot.online else "offline"
    online_class = "active" if snapshot.online else "offline"
    source_label = "новый снимок" if activity_result.created else "кэш до 2 минут"
    error_html = f'<p class="error">Последняя проверка: {escape(activity_result.error)}</p>' if activity_result.error else ""
    return f"""
        <div class="stats compact-stats">
          <div class="stat"><span>Статус</span><strong><span class="badge {online_class}">{online_label}</span></strong></div>
          <div class="stat"><span>Узел</span><strong>{escape(snapshot.node_id or "unknown")}</strong></div>
          <div class="stat"><span>Inbound-ов ключа</span><strong>{snapshot.inbound_count}</strong></div>
          <div class="stat"><span>Активных inbound-ов</span><strong>{snapshot.active_inbounds}</strong></div>
          <div class="stat"><span>Клиентов найдено</span><strong>{snapshot.matched_clients}</strong></div>
          <div class="stat"><span>Трафик</span><strong>{format_bytes(snapshot.up_bytes + snapshot.down_bytes)}</strong></div>
        </div>
        <p class="muted">Снято: {format_admin_dt(snapshot.sampled_at)} · {escape(source_label)}</p>
        {error_html}
    """


def render_key_activity_chart(activity: list[dict[str, object]]) -> str:
    if not activity:
        return '<p class="muted">Данных активности ключа пока нет.</p>'
    rows = []
    for item in activity:
        hours = float(item["hours"])
        width = max(1, int((hours / 24) * 100)) if hours > 0 else 0
        rows.append(
            '<div class="activity-row">'
            f'<span class="activity-date">{escape(str(item["date"]))}</span>'
            '<div class="activity-bar-track">'
            f'<div class="activity-bar" style="width: {width}%"></div>'
            "</div>"
            f'<span class="activity-hours">{hours:.1f} ч</span>'
            "</div>"
        )
    return '<div class="activity-chart">' + "\n".join(rows) + "</div>"


def favorite_action_text(user: User) -> str:
    actions = [
        ("Купить", int(user.buy_clicks or 0)),
        ("Поддержка", int(user.support_clicks or 0)),
        ("Замена", int(user.replace_key_clicks or 0)),
    ]
    label, count = max(actions, key=lambda item: item[1])
    return label if count > 0 else "нет"


def render_user_click_details(user: User) -> str:
    rows = [
        ("Купить", user.buy_clicks, user.last_buy_clicked_at),
        ("Поддержка", user.support_clicks, user.last_support_clicked_at),
        ("Замена sub", user.replace_key_clicks, user.last_replace_key_clicked_at),
        ("Успешная замена", user.replace_key_successes, user.last_replace_key_completed_at),
    ]
    items = "\n".join(
        f'<div class="stat"><span>{escape(label)}</span><strong>{int(count or 0)}</strong><span class="muted">последний раз: {format_admin_dt(last_at)}</span></div>'
        for label, count, last_at in rows
    )
    return f'<div class="stats compact-stats">{items}</div>'


def render_detail_feedback_rows(feedbacks: list[UserFeedback]) -> str:
    if not feedbacks:
        return table_empty("Отзывов пока нет")
    rows = ["<thead><tr><th>Дата</th><th>Оценка</th><th>Текст</th><th>Админу</th></tr></thead><tbody>"]
    for feedback in feedbacks:
        rows.append(
            f"<tr><td>{format_admin_dt(feedback.created_at)}</td>"
            f"<td><span class=\"badge\">{escape(feedback_rating_admin_label(feedback.rating))}</span></td>"
            f"<td>{escape(feedback.text or 'без текста')}</td>"
            f"<td>{format_admin_dt(feedback.sent_to_admin_at) if feedback.sent_to_admin_at else 'нет'}</td></tr>"
        )
    rows.append("</tbody>")
    return "\n".join(rows)


def render_detail_subscription_rows(subscriptions: list[Subscription]) -> str:
    ordered = sorted(subscriptions, key=lambda item: item.expires_at, reverse=True)
    if not ordered:
        return table_empty("Подписок нет")
    rows = ["<thead><tr><th>ID</th><th>Тариф</th><th>Статус</th><th>Старт</th><th>Финиш</th><th>Лимит</th></tr></thead><tbody>"]
    for subscription in ordered:
        traffic = "без лимита" if subscription.traffic_limit_gb is None else f"{subscription.traffic_limit_gb} ГБ"
        rows.append(
            f"<tr><td>{subscription.id}</td><td><code>{escape(subscription.plan_code)}</code></td>"
            f"<td><span class=\"badge\">{escape(subscription.status)}</span></td>"
            f"<td>{format_admin_dt(subscription.starts_at)}</td><td>{format_admin_dt(subscription.expires_at)}</td>"
            f"<td>{escape(traffic)}</td></tr>"
        )
    rows.append("</tbody>")
    return "\n".join(rows)


def render_detail_key_rows(keys: list[VpnKey]) -> str:
    private_keys = sorted([key for key in keys if key.key_type == "private"], key=lambda item: item.created_at, reverse=True)
    if not private_keys:
        return table_empty("Ключей нет")
    rows = ["<thead><tr><th>ID</th><th>Email</th><th>Сервер</th><th>Inbound-ы</th><th>IP limit</th><th>Статус</th><th>Создан</th><th>До</th></tr></thead><tbody>"]
    for key in private_keys:
        inbounds = ", ".join(str(item) for item in (key.x3ui_inbound_ids or [])) or "нет"
        status = "active" if key.active else "off"
        rows.append(
            f"<tr><td>{key.id}</td><td><code>{escape(key.email)}</code></td>"
            f"<td>{escape(key.server_label or 'нет')}</td><td><code>{escape(inbounds)}</code></td>"
            f"<td>{'' if key.limit_ip is None else key.limit_ip}</td>"
            f"<td><span class=\"badge {'active' if key.active else 'offline'}\">{status}</span></td>"
            f"<td>{format_admin_dt(key.created_at)}</td><td>{format_admin_dt(key.expires_at)}</td></tr>"
        )
    rows.append("</tbody>")
    return "\n".join(rows)


def render_detail_order_rows(orders: list[Order]) -> str:
    ordered = sorted(orders, key=lambda item: item.created_at, reverse=True)
    if not ordered:
        return table_empty("Заказов нет")
    rows = ["<thead><tr><th>ID</th><th>Тариф</th><th>Сумма</th><th>Статус</th><th>Провайдер</th><th>Создан</th><th>Оплачен</th></tr></thead><tbody>"]
    for order in ordered[:50]:
        rows.append(
            f"<tr><td>{order.id}</td><td><code>{escape(order.plan_code)}</code></td><td>{order.amount_rub} RUB</td>"
            f"<td><span class=\"badge\">{escape(order.status)}</span></td><td>{escape(order.payment_provider)}</td>"
            f"<td>{format_admin_dt(order.created_at)}</td><td>{format_admin_dt(order.paid_at)}</td></tr>"
        )
    rows.append("</tbody>")
    return "\n".join(rows)


def render_detail_profile_rows(profiles: list[DirectUserProfile]) -> str:
    if not profiles:
        return table_empty("Direct-профилей нет")
    rows = ["<thead><tr><th>ID</th><th>Node</th><th>Template</th><th>Inbound</th><th>Port</th><th>Статус</th><th>Обновлён</th></tr></thead><tbody>"]
    for profile in profiles:
        rows.append(
            f"<tr><td>{profile.id}</td><td><code>{escape(profile.node_id)}</code></td>"
            f"<td>{escape(profile.template_code)}</td><td>{profile.inbound_id or ''}</td><td>{profile.port}</td>"
            f"<td><span class=\"badge\">{escape(profile.status)}</span></td><td>{format_admin_dt(profile.updated_at)}</td></tr>"
        )
    rows.append("</tbody>")
    return "\n".join(rows)


def render_user_action_dialogs(
    user: User,
    token_qs: str,
    node_options: str,
    plan_options: str,
    username: str,
) -> str:
    issue_key_url = f"/admin/users/{user.id}/issue-key{token_qs}"
    delete_url = f"/admin/users/{user.id}/delete-access{token_qs}"
    return f"""
      <dialog id="delete-dialog">
        <form method="post" id="delete-form" action="{escape(delete_url)}">
          <input type="hidden" name="return_to" value="detail">
          <h2>Удалить доступ?</h2>
          <p id="delete-user-label" class="muted">{username} / {user.telegram_id}</p>
          <label class="checkline">
            <input type="checkbox" name="delete_inbounds" value="1">
            Удалить также direct inbound-ы пользователя
          </label>
          <p class="muted">Без галочки будет удалён только активный клиент/ключ, а inbound-слоты останутся закреплены за пользователем.</p>
          <div class="actions">
            <button class="danger" type="submit">Удалить</button>
            <button class="secondary" type="button" onclick="closeDeleteDialog()">Отмена</button>
          </div>
        </form>
      </dialog>
      <dialog id="issue-key-dialog">
        <form method="post" id="issue-key-form" action="{escape(issue_key_url)}">
          <input type="hidden" name="return_to" value="detail">
          <h2>Выдать ключ?</h2>
          <p id="issue-key-user-label" class="muted">{username} / {user.telegram_id}</p>
          <label>Узел / страна<select name="node_id">{node_options}</select></label>
          <label>Тариф<select name="plan_code">{plan_options}</select></label>
          <p class="muted">Можно оставить текущую подписку или сразу назначить выбранный тариф. Бот создаст новый sub и отправит его в Telegram.</p>
          <div class="actions">
            <button type="submit">Да, выдать</button>
            <button class="secondary" type="button" onclick="closeIssueKeyDialog()">Нет</button>
          </div>
        </form>
      </dialog>
    """


def render_user_action_scripts() -> str:
    return """
    <script>
      const dialog = document.getElementById('delete-dialog');
      const form = document.getElementById('delete-form');
      const label = document.getElementById('delete-user-label');
      const issueDialog = document.getElementById('issue-key-dialog');
      const issueForm = document.getElementById('issue-key-form');
      const issueLabel = document.getElementById('issue-key-user-label');
      function openDeleteDialog(button) {
        form.action = button.dataset.action;
        label.textContent = button.dataset.user;
        form.querySelector('input[name="delete_inbounds"]').checked = false;
        if (dialog.showModal) dialog.showModal(); else if (confirm('Удалить доступ пользователя?')) form.submit();
      }
      function closeDeleteDialog() {
        dialog.close();
      }
      function openIssueKeyDialog(button) {
        issueForm.action = button.dataset.action;
        issueLabel.textContent = button.dataset.user;
        if (issueDialog.showModal) issueDialog.showModal(); else if (confirm('Выдать ключ пользователю?')) issueForm.submit();
      }
      function closeIssueKeyDialog() {
        issueDialog.close();
      }
    </script>
    """


def render_admin_user_filters(request: Request, token: str, rows: list[AdminUserMonitorRow]) -> str:
    params = request.query_params
    q = str(params.get("q") or "")
    plan = str(params.get("plan") or "all")
    key_state = str(params.get("key") or "all")
    server = str(params.get("server") or "all")
    sort = str(params.get("sort") or "created_desc")
    per_page = str(parse_page_size(str(params.get("per_page") or "50")))
    server_options = [("all", "Все серверы")]
    server_options.extend((label, label) for label in sorted({row.server_label for row in rows if row.server_label != "нет ключа"}))
    return f"""
        <form method="get" action="/admin/users" class="grid filters">
          {hidden_token_input(token)}
          <label class="search-field">Поиск по всем пользователям<input name="q" value="{escape(q)}" placeholder="@username, Telegram ID, user ID"></label>
          <label>Подписка<select name="plan">{option_tags([("all", "Все"), ("paid", "Платные"), ("free", "Бесплатные trial"), ("expired", "Истекшие"), ("none", "Без подписки")], plan)}</select></label>
          <label>Ключ<select name="key">{option_tags([("all", "Все"), ("active", "Активный"), ("expired", "Истёк / отключён"), ("no_key", "Нет ключа")], key_state)}</select></label>
          <label>Сервер<select name="server">{option_tags(server_options, server)}</select></label>
          <label>Сортировка<select name="sort">{option_tags([("created_desc", "Новые сверху"), ("buy_desc", "Купить: больше сверху"), ("support_desc", "Поддержка: больше сверху"), ("replace_desc", "Замены: больше сверху"), ("paid_orders_desc", "Оплат: больше сверху"), ("inbounds_desc", "Inbound-ы: больше сверху"), ("expires_asc", "Истекают раньше")], sort)}</select></label>
          <label>На странице<select name="per_page">{option_tags([("25", "25"), ("50", "50"), ("100", "100")], per_page)}</select></label>
          <div class="actions">
            <button type="submit">Показать</button>
            <a class="button secondary" href="/admin/users{('?' + urlencode({'token': token})) if token else ''}">Сбросить</a>
          </div>
        </form>
    """


def render_admin_user_pagination(request: Request, token: str, total: int, page: int, per_page: int) -> str:
    total_pages = max(1, (total + per_page - 1) // per_page)
    if total_pages <= 1:
        return '<p class="muted pager">Все найденные пользователи помещаются на одной странице.</p>'

    def page_url(target_page: int) -> str:
        params = dict(request.query_params)
        if token:
            params["token"] = token
        params["page"] = str(target_page)
        params["per_page"] = str(per_page)
        return f"/admin/users?{urlencode(params)}"

    prev_link = (
        f'<a class="button secondary" href="{escape(page_url(page - 1))}">Назад</a>'
        if page > 1
        else '<span class="button secondary disabled">Назад</span>'
    )
    next_link = (
        f'<a class="button secondary" href="{escape(page_url(page + 1))}">Дальше</a>'
        if page < total_pages
        else '<span class="button secondary disabled">Дальше</span>'
    )
    return (
        '<div class="pager">'
        f"{prev_link}"
        f"<span>Страница <strong>{page}</strong> из <strong>{total_pages}</strong></span>"
        f"{next_link}"
        "</div>"
    )


def direct_node_issue_options(nodes: list[DirectNodeConfig]) -> str:
    available = [
        node
        for node in nodes
        if node.node_id and node.api_base_url and node.api_username and node.api_password and (node.public_host or node.public_ip)
    ]
    options = [
        f'<option value="{SYSTEM_NODE_AUTO}">Авто — самый свободный основной сервер</option>'
    ]
    options.extend(
        f'<option value="{escape(node.node_id)}">{escape(node.country_flag)} {escape(node.name)} ({escape(node.country_code)}, {escape(node.node_id)})</option>'
        for node in available
    )
    return "\n".join(options)


async def admin_issue_plans(session: AsyncSession) -> list[Plan]:
    return list(
        (
            await session.scalars(
                select(Plan)
                .where(Plan.is_active.is_(True), Plan.code != "admin_test")
                .order_by(Plan.price_rub, Plan.days, Plan.code)
            )
        ).all()
    )


def admin_issue_plan_options(plans: list[Plan]) -> str:
    options = ['<option value="">Текущая активная подписка — не менять</option>']
    options.extend(
        f'<option value="{escape(plan.code)}">{escape(plan.title)} — {plan.days} дн., {plan.price_rub} ₽</option>'
        for plan in plans
    )
    return "\n".join(options)


def render_admin_user_row(row: AdminUserMonitorRow, token_qs: str) -> str:
    user = row.user
    username = f"@{escape(user.username)}" if user.username else "без username"
    first_name = escape(user.first_name or "")
    created = format_admin_dt(user.created_at)
    sub_label = subscription_admin_label(row.subscription, row.plan_kind)
    sub_until = format_admin_dt(row.subscription.expires_at) if row.subscription else "нет"
    key_label = key_admin_label(row.key, row.key_state)
    key_until = format_admin_dt(row.key.expires_at) if row.key and row.key.expires_at else "без срока"
    action_url = f"/admin/users/{user.id}/delete-access{token_qs}"
    issue_key_url = f"/admin/users/{user.id}/issue-key{token_qs}"
    detail_url = f"/admin/users/{user.id}{token_qs}"
    return f"""<tr>
      <td data-label="Пользователь">
        <a class="user-link" href="{escape(detail_url)}"><strong>{username}</strong></a><br>
        <span class="muted">{first_name}</span><br>
        <code>{user.telegram_id}</code><br>
        <span class="muted">ID {user.id}, с {created}</span>
      </td>
      <td data-label="Подписка">
        {sub_label}<br>
        <span class="muted">до {sub_until}</span><br>
        <span class="muted">оплат: {row.paid_orders}/{row.total_orders}</span>
      </td>
      <td data-label="Ключ / сервер">
        {key_label}<br>
        <span class="muted">{escape(row.server_label)}</span><br>
        <span class="muted">до {key_until}</span><br>
        <span class="muted">истёкших ключей: {row.expired_keys}</span>
      </td>
      <td data-label="Inbound-ы">
        <strong>{row.assigned_inbounds}</strong><br>
        <span class="muted">direct профилей: {row.direct_profiles}</span>
      </td>
      <td data-label="Действия">
        Купить: <strong>{int(user.buy_clicks or 0)}</strong><br>
        Поддержка: <strong>{int(user.support_clicks or 0)}</strong><br>
        Замена: <strong>{int(user.replace_key_clicks or 0)}</strong><br>
        Успешно: <strong>{int(user.replace_key_successes or 0)}</strong><br>
        Отзывы: <strong>{row.feedback_count}</strong><br>
        <span class="muted">последний: {escape(feedback_rating_admin_label(row.last_feedback_rating))}</span>
      </td>
      <td data-label="Управление">
        <a class="button secondary icon-button" href="{escape(detail_url)}" title="Статистика пользователя">!</a>
        <button class="secondary icon-button" type="button" title="Выдать ключ" data-action="{escape(issue_key_url)}" data-user="{username} / {user.telegram_id}" onclick="openIssueKeyDialog(this)">🔑</button>
        <button class="danger" type="button" data-action="{escape(action_url)}" data-user="{username} / {user.telegram_id}" onclick="openDeleteDialog(this)">Удалить доступ</button>
      </td>
    </tr>"""


def option_tags(options: list[tuple[str, str]], selected: str) -> str:
    return "\n".join(
        f'<option value="{escape(value)}"{" selected" if value == selected else ""}>{escape(label)}</option>'
        for value, label in options
    )


def feedback_rating_admin_label(value: str | None) -> str:
    return {
        "excellent": "отлично",
        "good": "хорошо",
        "satisfactory": "удовлетворительно",
        "custom": "сам напешууууУ",
        "delivery_forbidden": "бот заблокирован",
        "empty": "без оценки",
        "": "нет",
    }.get(value or "", value or "нет")


def subscription_admin_label(subscription: Subscription | None, plan_kind: str) -> str:
    if subscription is None:
        return '<span class="badge">нет</span>'
    css = "active" if plan_kind in {"paid", "free"} else "offline" if plan_kind == "expired" else ""
    label = {
        "paid": "платная",
        "free": "trial",
        "expired": "истекла",
        "none": "нет",
    }.get(plan_kind, plan_kind)
    return f'<span class="badge {css}">{escape(label)}</span> <code>{escape(subscription.plan_code)}</code>'


def key_admin_label(key: VpnKey | None, key_state: str) -> str:
    if key is None:
        return '<span class="badge">нет ключа</span>'
    css = "active" if key_state == "active" else "offline"
    return f'<span class="badge {css}">{escape(key_state)}</span> <code>{escape(key.email)}</code>'


def format_admin_dt(value) -> str:
    return value.strftime("%d.%m.%Y %H:%M UTC") if value else "нет"


def format_bytes(value: int) -> str:
    amount = float(max(0, int(value or 0)))
    units = ["B", "KB", "MB", "GB", "TB"]
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} {unit}"
        amount /= 1024
    return f"{amount:.1f} TB"


def render_direct_nodes_page(
    nodes: list[DirectNodeConfig],
    token: str,
    *,
    form_config: DirectNodeConfig | None = None,
    checks: list[object] | None = None,
    notice: str = "",
    error: str = "",
) -> str:
    token_qs = f"?{urlencode({'token': token})}" if token else ""
    config = form_config or default_direct_node_form()
    rows = "\n".join(render_direct_node_row(node) for node in nodes) or table_empty("Direct-node узлов пока нет")
    checks_html = render_direct_node_checks(checks or [])
    notice_html = f'<p class="muted">{escape(notice)}</p>' if notice else ""
    error_html = f'<p class="error">{escape(error)}</p>' if error else ""
    return f"""<!doctype html>
<html lang="ru">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Direct nodes</title>
    <style>{admin_simple_page_css()}</style>
  </head>
  <body>
    <main>
      <section class="panel">
        <p class="eyebrow">MiloshVPN Admin</p>
        <h1>Direct-node узлы</h1>
        <p class="muted">Здесь хранятся только настройки узлов. Inbound-ы сюда не вписываются: они создаются отдельно на 3x-ui узла для конкретного пользователя.</p>
        {admin_nav(token, "direct_nodes")}
      </section>

      <section class="panel">
        <h2>Подключённые узлы</h2>
        <table>
          <thead>
            <tr>
              <th>Источник</th>
              <th>Node</th>
              <th>Публичный адрес</th>
              <th>3x-ui</th>
              <th>Agent</th>
              <th>Порты</th>
              <th>Статус</th>
            </tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>
      </section>

      <section class="panel">
        <h2>Проверить и добавить узел</h2>
        {notice_html}
        {error_html}
        {checks_html}
        <form method="post" action="/admin/direct-nodes/check{token_qs}" class="grid" style="grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));">
          {direct_node_form_fields(config)}
          <div class="actions">
            <button type="submit" formaction="/admin/direct-nodes/check{token_qs}">Проверить соединение</button>
            <button class="secondary" type="submit" formaction="/admin/direct-nodes{token_qs}">Проверить и сохранить</button>
          </div>
        </form>
      </section>
    </main>
  </body>
</html>"""


def render_direct_node_row(node: DirectNodeConfig) -> str:
    api_state = "задано" if node.api_base_url and node.api_username and node.api_password else "неполно"
    agent_state = "задан" if node.agent_url else "не задан"
    public = node.public_host or node.public_ip
    return f"""<tr>
      <td><span class="badge">{escape(node.source)}</span></td>
      <td><code>{escape(node.node_id)}</code><br>{escape(node.country_flag)} {escape(node.name)} / {escape(node.country_code)}</td>
      <td>{escape(public or "не задан")}</td>
      <td>{escape(api_state)}<br><span class="muted">TLS verify: {escape(str(node.api_verify_tls).lower())}</span></td>
      <td>{escape(agent_state)}<br><span class="muted">token: {escape("задан" if node.agent_token else "пусто")}</span></td>
      <td><code>{node.vpn_port_min}-{node.vpn_port_max}</code></td>
      <td><span class="badge">{escape(node.status)}</span></td>
    </tr>"""


def render_direct_node_checks(checks: list[object]) -> str:
    if not checks:
        return ""
    lines = ['<div class="panel" style="margin: 0 0 14px; padding: 12px;"><h2 style="font-size: 16px;">Проверка</h2>']
    for check in checks:
        status = str(getattr(check, "status", ""))
        css = "active" if status == "ok" else "offline" if status == "error" else ""
        title = escape(str(getattr(check, "title", "")))
        detail = escape(str(getattr(check, "detail", "")))
        lines.append(f'<p><span class="badge {css}">{escape(status)}</span> <strong>{title}</strong>: {detail}</p>')
    lines.append("</div>")
    return "\n".join(lines)


def direct_node_form_fields(config: DirectNodeConfig) -> str:
    return f"""
          <label>Node ID<input name="node_id" value="{escape(config.node_id)}" placeholder="de-1"></label>
          <label>Название<input name="name" value="{escape(config.name)}" placeholder="Germany-1"></label>
          <label>Country code<input name="country_code" value="{escape(config.country_code)}" placeholder="DE"></label>
          <label>Flag<input name="country_flag" value="{escape(config.country_flag)}" placeholder="🇩🇪"></label>
          <label>Public host<input name="public_host" value="{escape(config.public_host)}" placeholder="de.example.com"></label>
          <label>Public IP<input name="public_ip" value="{escape(config.public_ip)}" placeholder="1.2.3.4"></label>
          <label>3x-ui URL<input name="api_base_url" value="{escape(config.api_base_url)}" placeholder="https://host/panel-path"></label>
          <label>3x-ui username<input name="api_username" value="{escape(config.api_username)}"></label>
          <label>3x-ui password<input type="password" name="api_password" value="{escape(config.api_password)}"></label>
          <label>3x-ui verify TLS<select name="api_verify_tls">{bool_options(config.api_verify_tls)}</select></label>
          <label>3x-ui timeout seconds<input name="api_timeout_seconds" value="{config.api_timeout_seconds}" inputmode="numeric"></label>
          <label>Agent URL<input name="agent_url" value="{escape(config.agent_url)}" placeholder="https://node-agent.example.com"></label>
          <label>Agent token<input type="password" name="agent_token" value="{escape(config.agent_token)}"></label>
          <label>Agent verify TLS<select name="agent_verify_tls">{bool_options(config.agent_verify_tls)}</select></label>
          <label>Agent timeout seconds<input name="agent_timeout_seconds" value="{config.agent_timeout_seconds}" inputmode="numeric"></label>
          <label>VPN port min<input name="vpn_port_min" value="{config.vpn_port_min}" inputmode="numeric"></label>
          <label>VPN port max<input name="vpn_port_max" value="{config.vpn_port_max}" inputmode="numeric"></label>
          <label>Reserved ports<input name="reserved_ports" value="{escape(config.reserved_ports)}" placeholder="22,443,6881-6999"></label>
    """


def bool_options(value: bool) -> str:
    true_selected = " selected" if value else ""
    false_selected = "" if value else " selected"
    return f'<option value="false"{false_selected}>false</option><option value="true"{true_selected}>true</option>'


def default_direct_node_form() -> DirectNodeConfig:
    settings = get_settings()
    return DirectNodeConfig(
        source="form",
        node_id=settings.node_de_1_id,
        name=settings.node_de_1_name,
        country_code=settings.node_de_1_country_code,
        country_flag=settings.node_de_1_country_flag,
        public_host=settings.node_de_1_public_host,
        public_ip=settings.node_de_1_public_ip,
        api_base_url=settings.node_de_1_3xui_base_url,
        api_username=settings.node_de_1_3xui_username,
        api_password="",
        api_verify_tls=settings.node_de_1_3xui_verify_tls,
        api_timeout_seconds=settings.node_de_1_3xui_timeout_seconds,
        agent_url=settings.node_de_1_agent_url,
        agent_token="",
        agent_verify_tls=settings.node_de_1_agent_verify_tls,
        agent_timeout_seconds=settings.node_de_1_agent_timeout_seconds,
        vpn_port_min=settings.node_de_1_vpn_port_min,
        vpn_port_max=settings.node_de_1_vpn_port_max,
        reserved_ports=settings.node_de_1_reserved_ports,
    )


def direct_node_config_from_form(form: object) -> DirectNodeConfig:
    def text(name: str, default: str = "") -> str:
        return str(form.get(name) or default).strip()

    return DirectNodeConfig(
        source="form",
        node_id=text("node_id", "de-1"),
        name=text("name", "Germany-1"),
        country_code=text("country_code", "DE"),
        country_flag=text("country_flag", "🇩🇪"),
        public_host=text("public_host"),
        public_ip=text("public_ip"),
        api_base_url=text("api_base_url"),
        api_username=text("api_username"),
        api_password=text("api_password"),
        api_verify_tls=parse_bool(text("api_verify_tls")),
        api_timeout_seconds=parse_positive_int(text("api_timeout_seconds"), 20),
        agent_url=text("agent_url"),
        agent_token=text("agent_token"),
        agent_verify_tls=parse_bool(text("agent_verify_tls")),
        agent_timeout_seconds=parse_positive_int(text("agent_timeout_seconds"), 20),
        vpn_port_min=parse_positive_int(text("vpn_port_min"), 30000),
        vpn_port_max=parse_positive_int(text("vpn_port_max"), 39999),
        reserved_ports=text("reserved_ports", "22,443,6881-6999"),
    )


def parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_positive_int(value: str, default: int) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def render_manual_order_grant_page(result: ManualOrderGrantResult, notified: bool, token: str) -> str:
    token_qs = f"?{urlencode({'token': token})}" if token else ""
    if result.granted:
        action = "Заказ отмечен оплаченным, подписка применена, sub создан."
    elif result.repaired_key:
        action = "Заказ уже был оплачен, отсутствующий sub пересоздан."
    elif result.was_already_paid:
        action = "Заказ уже был оплачен, срок повторно не продлевался."
    else:
        action = "Заказ проверен."
    notified_text = "Сообщение с sub отправлено пользователю." if notified else (
        "Сообщение отправить не удалось. Sub уже применён в профиле, можно написать пользователю вручную."
    )
    return f"""<!doctype html>
<html lang="ru">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Выдача ключа</title>
    <style>{admin_simple_page_css()}</style>
  </head>
  <body>
    <main>
      <section class="panel">
        <p class="eyebrow">MiloshVPN Admin</p>
        <h1>Ключ выдан</h1>
        <p>{escape(action)}</p>
        <dl>
          <div><dt>Заказ</dt><dd><code>{result.order_id}</code></dd></div>
          <div><dt>Telegram ID</dt><dd><code>{result.user_telegram_id}</code></dd></div>
          <div><dt>Тариф</dt><dd>{escape(result.plan_code)}</dd></div>
          <div><dt>Уведомление</dt><dd>{escape(notified_text)}</dd></div>
        </dl>
        <div class="actions">
          <a class="button" href="/admin{token_qs}">Вернуться в админку</a>
          <a class="button secondary" href="/admin/orders/search{token_qs}">Найти ещё</a>
        </div>
      </section>
    </main>
  </body>
</html>"""


def render_manual_order_error_page(error: str, token: str) -> str:
    token_qs = f"?{urlencode({'token': token})}" if token else ""
    return f"""<!doctype html>
<html lang="ru">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Ошибка выдачи</title>
    <style>{admin_simple_page_css()}</style>
  </head>
  <body>
    <main>
      <section class="panel">
        <p class="eyebrow">MiloshVPN Admin</p>
        <h1>Не получилось выдать ключ</h1>
        <p class="error">{escape(error)}</p>
        <a class="button" href="/admin{token_qs}">Вернуться в админку</a>
      </section>
    </main>
  </body>
</html>"""


def render_order_search_row(order: Order, token_qs: str) -> str:
    user = order.user
    username = f"@{escape(user.username)}" if user and user.username else ""
    telegram_id = user.telegram_id if user else ""
    plan_title = order.plan.title if order.plan else order.plan_code
    paid = order.paid_at.strftime("%d.%m.%Y %H:%M UTC") if order.paid_at else "не оплачено"
    return f"""<tr>
      <td>{order.id}</td>
      <td><code>{telegram_id}</code><br>{username}</td>
      <td>{escape(plan_title)}<br><span class="muted">{escape(order.plan_code)}</span></td>
      <td>{order.amount_rub} RUB</td>
      <td><span class="badge">{escape(order.status)}</span><br><span class="muted">{paid}</span></td>
      <td><code>{escape(order.payment_code)}</code></td>
      <td>
        <form method="post" action="/admin/orders/{order.id}/grant{token_qs}">
          <button type="submit">Выдать ключ</button>
        </form>
      </td>
    </tr>"""


def hidden_token_input(token: str) -> str:
    return f'<input type="hidden" name="token" value="{escape(token)}">' if token else ""


def admin_simple_page_css() -> str:
    return """
      :root {
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        color: #121417;
        background: #f5f7fa;
      }
      body { margin: 0; }
      main {
        max-width: 1100px;
        margin: 0 auto;
        padding: 24px;
      }
      .panel {
        background: #fff;
        border: 1px solid #dfe5ec;
        border-radius: 8px;
        padding: 18px;
        margin-bottom: 20px;
      }
      h1, h2 { margin: 0 0 16px; letter-spacing: 0; }
      h1 { font-size: 26px; }
      h2 { font-size: 20px; }
      .eyebrow {
        margin: 0 0 8px;
        color: #687385;
        font-size: 13px;
      }
      .grid {
        display: grid;
        grid-template-columns: minmax(220px, 1fr) auto;
        gap: 12px;
        align-items: end;
      }
      label {
        display: grid;
        gap: 6px;
        color: #566174;
        font-size: 13px;
      }
      input, select {
        box-sizing: border-box;
        width: 100%;
        border: 1px solid #cad2dc;
        border-radius: 6px;
        padding: 9px 10px;
        font: inherit;
        background: #fff;
      }
      table {
        width: 100%;
        border-collapse: collapse;
        font-size: 14px;
      }
      th, td {
        text-align: left;
        border-bottom: 1px solid #e6ebf1;
        padding: 10px 8px;
        vertical-align: top;
      }
      th {
        color: #566174;
        font-size: 12px;
        text-transform: uppercase;
      }
      button, .button {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-height: 38px;
        border: 0;
        border-radius: 6px;
        padding: 0 12px;
        font: inherit;
        cursor: pointer;
        background: #1563ff;
        color: #fff;
        text-decoration: none;
      }
      .button.secondary {
        background: #e9eef5;
        color: #121417;
      }
      button.secondary {
        background: #e9eef5;
        color: #121417;
      }
      .button.disabled {
        opacity: 0.55;
        pointer-events: none;
      }
      .actions {
        display: flex;
        gap: 8px;
        align-items: center;
        flex-wrap: wrap;
      }
      .badge {
        display: inline-block;
        border-radius: 999px;
        padding: 3px 8px;
        background: #e9eef5;
        color: #273244;
        font-size: 12px;
      }
      .muted {
        color: #687385;
        font-size: 12px;
      }
      .error {
        color: #a51d2d;
      }
      code {
        word-break: break-all;
      }
      dl {
        display: grid;
        gap: 10px;
        margin: 16px 0;
      }
      dl div {
        display: grid;
        grid-template-columns: 130px 1fr;
        gap: 12px;
      }
      dt {
        color: #687385;
      }
      dd {
        margin: 0;
      }
      @media (max-width: 700px) {
        main { padding: 14px; }
        .grid { grid-template-columns: 1fr; }
        table { display: block; overflow-x: auto; }
        dl div { grid-template-columns: 1fr; gap: 4px; }
      }
    """ + admin_nav_css()


def admin_users_page_css() -> str:
    return admin_simple_page_css() + """
      main {
        max-width: 1280px;
      }
      .filters {
        grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
      }
      .search-field {
        grid-column: span 2;
      }
      .list-head {
        display: flex;
        justify-content: space-between;
        gap: 12px;
        align-items: center;
        flex-wrap: wrap;
      }
      .list-head h2 {
        margin-bottom: 0;
      }
      .pager {
        display: flex;
        gap: 8px;
        align-items: center;
        justify-content: flex-end;
        flex-wrap: wrap;
        margin: 0 0 12px;
      }
      .panel > .pager:last-child {
        margin: 12px 0 0;
      }
      .icon-button {
        min-width: 42px;
        width: auto;
        font-size: 18px;
      }
      .user-link {
        color: #1563ff;
        text-decoration: none;
      }
      .user-link:hover {
        text-decoration: underline;
      }
      .compact-stats {
        grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      }
      .subheading {
        margin-top: 18px;
        font-size: 17px;
      }
      .activity-chart {
        display: grid;
        gap: 8px;
      }
      .activity-row {
        display: grid;
        grid-template-columns: 54px minmax(120px, 1fr) 64px;
        gap: 10px;
        align-items: center;
      }
      .activity-date,
      .activity-hours {
        color: #687385;
        font-size: 12px;
      }
      .activity-hours {
        text-align: right;
      }
      .activity-bar-track {
        height: 12px;
        border-radius: 999px;
        background: #e9eef5;
        overflow: hidden;
      }
      .activity-bar {
        height: 100%;
        border-radius: inherit;
        background: #1563ff;
      }
      .detail-table {
        display: table;
      }
      .stat.wide {
        grid-column: span 2;
      }
      .stat.wide strong {
        font-size: 15px;
        line-height: 1.45;
      }
      .notice {
        margin: 0 0 14px;
        padding: 10px 12px;
        border-radius: 6px;
        background: #dff7e8;
        color: #116b35;
      }
      .users-table td:last-child {
        width: 150px;
      }
      dialog {
        width: min(92vw, 460px);
        border: 1px solid #dfe5ec;
        border-radius: 8px;
        padding: 18px;
        box-shadow: 0 24px 80px rgba(20, 28, 38, 0.18);
      }
      dialog::backdrop {
        background: rgba(18, 20, 23, 0.42);
      }
      .checkline {
        display: flex;
        grid-template-columns: none;
        align-items: center;
        gap: 10px;
        margin: 14px 0;
        color: #121417;
      }
      .checkline input {
        width: auto;
      }
      @media (max-width: 820px) {
        main {
          padding: 12px;
        }
        .stats {
          grid-template-columns: repeat(2, minmax(0, 1fr));
        }
        .stat.wide {
          grid-column: 1 / -1;
        }
        .users-table,
        .detail-table,
        .users-table thead,
        .detail-table thead,
        .users-table tbody,
        .detail-table tbody,
        .users-table tr,
        .detail-table tr,
        .users-table th,
        .detail-table th,
        .users-table td {
          display: block;
          width: 100%;
        }
        .detail-table td {
          display: block;
          width: 100%;
        }
        .users-table thead {
          display: none;
        }
        .detail-table thead {
          display: none;
        }
        .users-table tr,
        .detail-table tr {
          border: 1px solid #dfe5ec;
          border-radius: 8px;
          margin-bottom: 12px;
          padding: 8px 10px;
          background: #fff;
        }
        .users-table td,
        .detail-table td {
          border-bottom: 1px solid #edf1f5;
          padding: 9px 0;
        }
        .users-table td:last-child,
        .detail-table td:last-child {
          border-bottom: 0;
          width: 100%;
        }
        .users-table td::before {
          content: attr(data-label);
          display: block;
          margin-bottom: 4px;
          color: #687385;
          font-size: 12px;
          text-transform: uppercase;
        }
        button,
        .button {
          width: 100%;
        }
        .icon-button {
          width: 100%;
        }
        .search-field {
          grid-column: auto;
        }
        .pager {
          justify-content: stretch;
        }
        .pager span {
          flex: 1 1 100%;
          text-align: center;
        }
        .actions {
          width: 100%;
        }
        .activity-row {
          grid-template-columns: 48px 1fr;
        }
        .activity-hours {
          grid-column: 2;
          text-align: left;
        }
      }
      @media (max-width: 460px) {
        .stats {
          grid-template-columns: 1fr;
        }
        h1 {
          font-size: 23px;
        }
        .panel {
          padding: 14px;
        }
      }
    """


def render_stat(label: str, value: int) -> str:
    return f'<div class="stat"><span>{escape(label)}</span><strong>{value}</strong></div>'


def render_system_x3ui_status(state: dict[str, object]) -> str:
    status = str(state.get("status") or "unknown")
    nodes = state.get("nodes") if isinstance(state.get("nodes"), list) else []
    inbounds = state.get("inbounds") if isinstance(state.get("inbounds"), list) else []
    error = str(state.get("error") or "")
    status_class = "active" if status == "online" else "offline" if status == "offline" else ""
    enabled_nodes = sum(1 for node in nodes if isinstance(node, dict) and node.get("enable") is not False)
    online_nodes = sum(
        1
        for node in nodes
        if isinstance(node, dict)
        and node.get("enable") is not False
        and str(node.get("status") or "online").lower() == "online"
    )
    remote_inbounds = sum(1 for inbound in inbounds if isinstance(inbound, dict) and inbound.get("nodeId") is not None)
    local_inbounds = sum(1 for inbound in inbounds if isinstance(inbound, dict) and inbound.get("nodeId") is None)
    error_html = f'<p class="error">{escape(error)}</p>' if error else ""
    return f"""
        <p class="muted" style="margin-top: 0;">Бот использует системную 3x-ui, установленную на сервере, без Docker-контейнера 3x-ui.</p>
        <div class="stats">
          <div class="stat"><span>Статус панели</span><strong><span class="badge {status_class}">{escape(status)}</span></strong></div>
          <div class="stat"><span>Nodes online</span><strong>{online_nodes}/{enabled_nodes}</strong></div>
          <div class="stat"><span>User inbound’ы node</span><strong>{remote_inbounds}</strong></div>
          <div class="stat"><span>System inbound’ы admin</span><strong>{local_inbounds}</strong></div>
        </div>
        {error_html}
    """


def render_payment_provider_button(code: str, label: str, active_provider: str, token_qs: str) -> str:
    css_class = "secondary" if code != active_provider else ""
    suffix = " ✓" if code == active_provider else ""
    return (
        f'<form method="post" action="/admin/payment-mode{token_qs}">'
        f'<input type="hidden" name="provider" value="{escape(code)}">'
        f'<button class="{css_class}" type="submit">{escape(label)}{suffix}</button>'
        "</form>"
    )


def render_subscription_row(subscription: Subscription, key: VpnKey | None) -> str:
    user = subscription.user
    username = f"@{escape(user.username)}" if user.username else ""
    key_status = '<span class="badge active">active</span>' if key and key.active else '<span class="badge">no key</span>'
    return f"""<tr>
      <td>{subscription.id}</td>
      <td><code>{user.telegram_id}</code><br>{username}</td>
      <td>{escape(subscription.plan_code)}</td>
      <td>{subscription.expires_at:%d.%m.%Y %H:%M UTC}</td>
      <td>{key_status}</td>
    </tr>"""


def render_private_key_row(key: VpnKey, token_qs: str) -> str:
    user = key.user
    username = f"@{escape(user.username)}" if user and user.username else ""
    telegram_id = user.telegram_id if user else ""
    plan = key.subscription.plan_code if key.subscription else key.key_type
    expires = key.expires_at.strftime("%d.%m.%Y %H:%M UTC") if key.expires_at else "без срока"
    return f"""<tr>
      <td>{key.id}</td>
      <td><code>{telegram_id}</code><br>{username}</td>
      <td>{escape(plan)}</td>
      <td>{expires}</td>
      <td><code>{escape(key.email)}</code></td>
      <td>
        <form method="post" action="/admin/keys/{key.id}/revoke{token_qs}">
          <button class="danger" type="submit">Удалить</button>
        </form>
      </td>
    </tr>"""


def render_order_row(order: Order, token_qs: str) -> str:
    username = f"@{escape(order.user.username)}" if order.user and order.user.username else ""
    return f"""<tr>
      <td>{order.id}</td>
      <td><code>{order.user.telegram_id if order.user else ""}</code><br>{username}</td>
      <td>{escape(order.plan_code)}</td>
      <td>{order.amount_rub} RUB</td>
      <td><code>{escape(order.payment_code)}</code></td>
      <td>{order.expires_at:%d.%m.%Y %H:%M UTC}</td>
      <td>
        <form method="post" action="/admin/orders/{order.id}/grant{token_qs}">
          <button type="submit">Выдать ключ</button>
        </form>
      </td>
    </tr>"""


def table_empty(text: str) -> str:
    return f'<tr><td colspan="8">{escape(text)}</td></tr>'
