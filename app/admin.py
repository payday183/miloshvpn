from collections import Counter
from dataclasses import dataclass
from html import escape
from urllib.parse import urlencode

from aiogram import Bot
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db import get_session
from app.models import BotAdmin, DirectUserProfile, Order, Subscription, User, VpnKey
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
    list_direct_node_configs,
    release_direct_key_slots,
    save_direct_node_config,
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
from app.services.vpn import list_active_private_keys, revoke_key_remote, revoke_private_key
from app.timeutils import utcnow

router = APIRouter()


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
    return HTMLResponse(render_admin_users_page(sorted_rows, rows, request, token))


@router.post("/admin/users/{user_id}/delete-access")
async def admin_delete_user_access_action(
    user_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin_token),
) -> RedirectResponse:
    form = await request.form()
    delete_inbounds = str(form.get("delete_inbounds") or "").lower() in {"1", "true", "on", "yes"}
    result = await delete_user_access(session, user_id, delete_inbounds=delete_inbounds)
    await session.commit()
    token = request.query_params.get("token", "")
    notice = (
        f"Доступ удалён: ключей {result['keys']}, inbound-ов удалено {result['inbounds']}"
        if delete_inbounds
        else f"Ключ удалён: ключей {result['keys']}, inbound-ы оставлены за пользователем"
    )
    suffix = urlencode({"token": token, "notice": notice}) if token else urlencode({"notice": notice})
    return RedirectResponse(url=f"/admin/users?{suffix}", status_code=303)


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
            .limit(1000)
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
            )
        )
    return rows


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
    </style>
  </head>
  <body>
    <header>
      <h1>MiloshVPN Admin</h1>
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
    request: Request,
    token: str,
) -> str:
    token_qs = f"?{urlencode({'token': token})}" if token else ""
    notice = str(request.query_params.get("notice") or "")
    notice_html = f'<p class="notice">{escape(notice)}</p>' if notice else ""
    filters = render_admin_user_filters(request, token, all_rows)
    stats = render_admin_user_stats(all_rows)
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
        <div class="actions">
          <a class="button secondary" href="/admin{token_qs}">Главная</a>
          <a class="button secondary" href="/admin/direct-nodes{token_qs}">Direct nodes</a>
        </div>
      </section>

      <section class="stats">{stats}</section>

      <section class="panel">
        <h2>Фильтры</h2>
        {notice_html}
        {filters}
      </section>

      <section class="panel">
        <h2>Список: {len(rows)} из {len(all_rows)}</h2>
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
    </main>
    <script>
      const dialog = document.getElementById('delete-dialog');
      const form = document.getElementById('delete-form');
      const label = document.getElementById('delete-user-label');
      function openDeleteDialog(button) {{
        form.action = button.dataset.action;
        label.textContent = button.dataset.user;
        form.querySelector('input[name="delete_inbounds"]').checked = false;
        if (dialog.showModal) dialog.showModal(); else if (confirm('Удалить доступ пользователя?')) form.submit();
      }}
      function closeDeleteDialog() {{
        dialog.close();
      }}
    </script>
  </body>
</html>"""


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


def render_admin_user_filters(request: Request, token: str, rows: list[AdminUserMonitorRow]) -> str:
    params = request.query_params
    q = str(params.get("q") or "")
    plan = str(params.get("plan") or "all")
    key_state = str(params.get("key") or "all")
    server = str(params.get("server") or "all")
    sort = str(params.get("sort") or "created_desc")
    server_options = [("all", "Все серверы")]
    server_options.extend((label, label) for label in sorted({row.server_label for row in rows if row.server_label != "нет ключа"}))
    return f"""
        <form method="get" action="/admin/users" class="grid filters">
          {hidden_token_input(token)}
          <label>Поиск<input name="q" value="{escape(q)}" placeholder="@username, Telegram ID, user ID"></label>
          <label>Подписка<select name="plan">{option_tags([("all", "Все"), ("paid", "Платные"), ("free", "Бесплатные trial"), ("expired", "Истекшие"), ("none", "Без подписки")], plan)}</select></label>
          <label>Ключ<select name="key">{option_tags([("all", "Все"), ("active", "Активный"), ("expired", "Истёк / отключён"), ("no_key", "Нет ключа")], key_state)}</select></label>
          <label>Сервер<select name="server">{option_tags(server_options, server)}</select></label>
          <label>Сортировка<select name="sort">{option_tags([("created_desc", "Новые сверху"), ("buy_desc", "Купить: больше сверху"), ("support_desc", "Поддержка: больше сверху"), ("replace_desc", "Замены: больше сверху"), ("paid_orders_desc", "Оплат: больше сверху"), ("inbounds_desc", "Inbound-ы: больше сверху"), ("expires_asc", "Истекают раньше")], sort)}</select></label>
          <div class="actions">
            <button type="submit">Показать</button>
            <a class="button secondary" href="/admin/users{('?' + urlencode({'token': token})) if token else ''}">Сбросить</a>
          </div>
        </form>
    """


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
    return f"""<tr>
      <td data-label="Пользователь">
        <strong>{username}</strong><br>
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
        Успешно: <strong>{int(user.replace_key_successes or 0)}</strong>
      </td>
      <td data-label="Управление">
        <button class="danger" type="button" data-action="{escape(action_url)}" data-user="{username} / {user.telegram_id}" onclick="openDeleteDialog(this)">Удалить доступ</button>
      </td>
    </tr>"""


def option_tags(options: list[tuple[str, str]], selected: str) -> str:
    return "\n".join(
        f'<option value="{escape(value)}"{" selected" if value == selected else ""}>{escape(label)}</option>'
        for value, label in options
    )


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
        <div class="actions"><a class="button secondary" href="/admin{token_qs}">Назад</a></div>
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
      input {
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
    """


def admin_users_page_css() -> str:
    return admin_simple_page_css() + """
      main {
        max-width: 1280px;
      }
      .filters {
        grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
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
        .users-table thead,
        .users-table tbody,
        .users-table tr,
        .users-table th,
        .users-table td {
          display: block;
          width: 100%;
        }
        .users-table thead {
          display: none;
        }
        .users-table tr {
          border: 1px solid #dfe5ec;
          border-radius: 8px;
          margin-bottom: 12px;
          padding: 8px 10px;
          background: #fff;
        }
        .users-table td {
          border-bottom: 1px solid #edf1f5;
          padding: 9px 0;
        }
        .users-table td:last-child {
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
        .actions {
          width: 100%;
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
