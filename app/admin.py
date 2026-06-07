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
from app.models import BotAdmin, Order, Subscription, User, VpnKey, VpnNode
from app.services.admin_auth import (
    admin_panel_url_with_token,
    verify_admin_profile_signature,
    verify_telegram_login,
)
from app.services.admin_keys import create_admin_key
from app.services.billing import poll_donations
from app.services.expiry import expire_subscriptions, retry_expired_key_revokes
from app.services.manual_orders import ManualOrderError, ManualOrderGrantResult, manually_confirm_order, search_orders_for_admin
from app.services.node_monitor import format_bytes, local_key_counts, refresh_all_nodes, refresh_node_status
from app.services.nodes import activate_node, create_node, disable_node, list_nodes
from app.services.payment_notifications import notify_paid_order
from app.services.public_keys import rotate_public_key
from app.services.stats import collect_stats
from app.services.vpn import list_active_private_keys, revoke_private_key
from app.timeutils import utcnow

router = APIRouter()


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
    nodes = await list_nodes(session)
    node_counts = {node.id: await local_key_counts(session, node.id) for node in nodes}
    subscriptions = await active_subscriptions(session)
    pending_orders = await latest_pending_orders(session)
    private_keys = await list_active_private_keys(session, limit=100)
    return HTMLResponse(render_admin_page(stats, nodes, node_counts, subscriptions, pending_orders, private_keys, token))


@router.post("/admin/nodes")
async def create_node_action(
    request: Request,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin_token),
) -> RedirectResponse:
    form = await request.form()
    await create_node(
        session,
        title=str(form.get("title") or "New node"),
        mode=str(form.get("mode") or "mock"),
        base_url=str(form.get("base_url") or "http://x3ui:2053"),
        username=str(form.get("username") or "admin"),
        password=str(form.get("password") or "admin"),
        inbound_id=int(str(form.get("inbound_id") or "1")),
        max_clients=int(str(form.get("max_clients") or "10")),
        public_host=str(form.get("public_host") or "127.0.0.1"),
        public_port=int(str(form.get("public_port") or "8443")),
        vless_query=str(form.get("vless_query") or "type=tcp&security=none"),
        activate=str(form.get("activate") or "") == "on",
    )
    return redirect_to_admin(request)


@router.post("/admin/nodes/{node_id}/activate")
async def activate_node_action(
    node_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin_token),
) -> RedirectResponse:
    await activate_node(session, node_id)
    return redirect_to_admin(request)


@router.post("/admin/nodes/{node_id}/disable")
async def disable_node_action(
    node_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin_token),
) -> RedirectResponse:
    await disable_node(session, node_id)
    return redirect_to_admin(request)


@router.post("/admin/nodes/{node_id}/refresh")
async def refresh_node_action(
    node_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin_token),
) -> RedirectResponse:
    node = await session.get(VpnNode, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found")
    await refresh_node_status(session, node)
    await session.commit()
    return redirect_to_admin(request)


@router.post("/admin/nodes/refresh")
async def refresh_nodes_action(
    request: Request,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin_token),
) -> RedirectResponse:
    await refresh_all_nodes(session)
    return redirect_to_admin(request)


@router.post("/admin/donations/poll")
async def poll_donations_action(
    request: Request,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin_token),
) -> RedirectResponse:
    await poll_donations(session)
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


@router.get("/api/admin/nodes")
async def api_nodes(
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin_token),
) -> list[dict[str, object]]:
    nodes = await list_nodes(session)
    return [serialize_node(node, await local_key_counts(session, node.id)) for node in nodes]


@router.get("/api/admin/subscriptions")
async def api_subscriptions(
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin_token),
) -> list[dict[str, object]]:
    rows = await active_subscriptions(session)
    return [serialize_subscription(subscription, key) for subscription, key in rows]


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
                selectinload(Subscription.keys).selectinload(VpnKey.node),
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


def serialize_node(node: VpnNode, counts: dict[str, int]) -> dict[str, object]:
    return {
        "id": node.id,
        "title": node.title,
        "mode": node.mode,
        "base_url": node.base_url,
        "inbound_id": node.inbound_id,
        "max_clients": node.max_clients,
        "public_host": node.public_host,
        "public_port": node.public_port,
        "is_active": node.is_active,
        "status": node.status,
        "last_checked_at": node.last_checked_at.isoformat() if node.last_checked_at else None,
        "last_error": node.last_error,
        "latency_ms": node.last_latency_ms,
        "local_keys_total": counts["total"],
        "local_keys_active": counts["active"],
        "remote_clients": node.remote_clients,
        "remote_enabled_clients": node.remote_enabled_clients,
        "traffic_up_bytes": node.traffic_up_bytes,
        "traffic_down_bytes": node.traffic_down_bytes,
        "cpu_percent": node.cpu_percent,
        "memory_percent": node.memory_percent,
        "disk_percent": node.disk_percent,
    }


def serialize_subscription(subscription: Subscription, key: VpnKey | None) -> dict[str, object]:
    user = subscription.user
    return {
        "id": subscription.id,
        "telegram_id": user.telegram_id,
        "username": user.username,
        "plan": subscription.plan_code,
        "expires_at": subscription.expires_at.isoformat(),
        "node": key.node.title if key and key.node else None,
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
    nodes: list[VpnNode],
    node_counts: dict[int, dict[str, int]],
    subscriptions: list[tuple[Subscription, VpnKey | None]],
    pending_orders: list[Order],
    private_keys: list[VpnKey],
    token: str,
) -> str:
    settings = get_settings()
    token_qs = f"?{urlencode({'token': token})}" if token else ""
    token_input = hidden_token_input(token)
    default_admin_id = str(settings.admin_ids[0]) if settings.admin_ids else ""
    node_rows = "\n".join(render_node_row(node, node_counts.get(node.id, {}), token_qs) for node in nodes) or table_empty(
        "Нод пока нет"
    )
    sub_rows = "\n".join(render_subscription_row(subscription, key) for subscription, key in subscriptions) or table_empty(
        "Активных подписок пока нет"
    )
    key_rows = "\n".join(render_private_key_row(key, token_qs) for key in private_keys) or table_empty(
        "Личных активных ключей пока нет"
    )
    order_rows = "\n".join(render_order_row(order, token_qs) for order in pending_orders) or table_empty("Ожидающих оплат нет")
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
        <h2>Админские ключи</h2>
        <form method="post" action="/admin/admin-keys{token_qs}" class="grid">
          <label>Telegram ID админа<input name="telegram_id" value="{escape(default_admin_id)}" inputmode="numeric"></label>
          <div class="actions"><button type="submit">Создать admin key</button></div>
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
        <h2>Ноды</h2>
        <div class="actions" style="margin-bottom: 12px;">
          <form method="post" action="/admin/nodes/refresh{token_qs}"><button type="submit">Обновить статус всех нод</button></form>
        </div>
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Название</th>
              <th>3x-ui</th>
              <th>VLESS</th>
              <th>Статус</th>
              <th>Ключи</th>
              <th>Трафик</th>
              <th>Нагрузка</th>
              <th></th>
            </tr>
          </thead>
          <tbody>{node_rows}</tbody>
        </table>
      </section>

      <section class="panel">
        <h2>Подключить ноду</h2>
        <form method="post" action="/admin/nodes{token_qs}" class="grid">
          <label>Название<input name="title" value="New 3x-ui node"></label>
          <label>Режим
            <select name="mode">
              <option value="mock">mock</option>
              <option value="live">live</option>
            </select>
          </label>
          <label>3x-ui URL<input name="base_url" value="http://x3ui:2053"></label>
          <label>Логин<input name="username" value="admin"></label>
          <label>Пароль<input name="password" value="admin" type="password"></label>
          <label>Inbound ID<input name="inbound_id" value="1" type="number"></label>
          <label>Max clients<input name="max_clients" value="10" type="number"></label>
          <label>Public host<input name="public_host" value="127.0.0.1"></label>
          <label>Public port<input name="public_port" value="8443" type="number"></label>
          <label>VLESS query<input name="vless_query" value="type=tcp&security=none"></label>
          <label><input name="activate" type="checkbox" checked> Сделать активной</label>
          <div class="actions"><button type="submit">Добавить ноду</button></div>
        </form>
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
              <th>Нода</th>
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
              <th>Нода</th>
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


def render_admin_key_page(key: VpnKey, telegram_id: int, token: str) -> str:
    token_qs = f"?{urlencode({'token': token})}" if token else ""
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
        <h1>Админский ключ создан</h1>
        <dl>
          <div><dt>Telegram ID</dt><dd><code>{telegram_id}</code></dd></div>
          <div><dt>Label</dt><dd><code>{escape(key.email)}</code></dd></div>
        </dl>
        <p class="muted">VLESS ключ</p>
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


def render_manual_order_grant_page(result: ManualOrderGrantResult, notified: bool, token: str) -> str:
    token_qs = f"?{urlencode({'token': token})}" if token else ""
    if result.granted:
        action = "Заказ отмечен оплаченным, подписка применена, ключ создан."
    elif result.repaired_key:
        action = "Заказ уже был оплачен, отсутствующий ключ пересоздан."
    elif result.was_already_paid:
        action = "Заказ уже был оплачен, срок повторно не продлевался."
    else:
        action = "Заказ проверен."
    notified_text = "Сообщение с ключом отправлено пользователю." if notified else (
        "Сообщение отправить не удалось. Ключ уже применён в профиле, можно написать пользователю вручную."
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


def render_stat(label: str, value: int) -> str:
    return f'<div class="stat"><span>{escape(label)}</span><strong>{value}</strong></div>'


def render_node_row(node: VpnNode, counts: dict[str, int], token_qs: str) -> str:
    active = '<span class="badge active">active</span>' if node.is_active else '<span class="badge">off</span>'
    status_class = "active" if node.status == "online" else "offline" if node.status == "offline" else ""
    status = f'<span class="badge {status_class}">{escape(node.status)}</span>'
    checked = node.last_checked_at.strftime("%d.%m %H:%M UTC") if node.last_checked_at else "еще не проверялась"
    error = f'<br><span class="muted">{escape(node.last_error)}</span>' if node.last_error else ""
    local_total = counts.get("total", 0)
    local_active = counts.get("active", 0)
    private_active = counts.get("private_active", 0)
    public_active = counts.get("public_active", 0)
    traffic = format_bytes(node.traffic_up_bytes + node.traffic_down_bytes)
    load = "<br>".join(
        [
            f"CPU: {percent_text(node.cpu_percent)}",
            f"RAM: {percent_text(node.memory_percent)}",
            f"Disk: {percent_text(node.disk_percent)}",
        ]
    )
    action = (
        f'<form method="post" action="/admin/nodes/{node.id}/disable{token_qs}">'
        '<button class="danger" type="submit">Отключить</button></form>'
        if node.is_active
        else f'<form method="post" action="/admin/nodes/{node.id}/activate{token_qs}">'
        '<button type="submit">Активировать</button></form>'
    )
    return f"""<tr>
      <td>{node.id}</td>
      <td>{escape(node.title)}<br><span class="badge">{escape(node.mode)}</span> {active}</td>
      <td><code>{escape(node.base_url)}</code><br>inbound {node.inbound_id}<br><span class="muted">limit {node.max_clients}</span></td>
      <td><code>{escape(node.public_host)}:{node.public_port}</code></td>
      <td>{status}<br><span class="muted">{checked}</span>{error}<br><span class="muted">{node.last_latency_ms or 0} ms</span></td>
      <td>
        local: {local_active}/{local_total}<br>
        private: {private_active}<br>
        free: {public_active}<br>
        3x-ui: {node.remote_enabled_clients}/{node.remote_clients}
      </td>
      <td>
        total: {traffic}<br>
        up: {format_bytes(node.traffic_up_bytes)}<br>
        down: {format_bytes(node.traffic_down_bytes)}
      </td>
      <td>{load}</td>
      <td><div class="actions">{action}<form method="post" action="/admin/nodes/{node.id}/refresh{token_qs}"><button class="secondary" type="submit">Статус</button></form></div></td>
    </tr>"""


def render_subscription_row(subscription: Subscription, key: VpnKey | None) -> str:
    user = subscription.user
    username = f"@{escape(user.username)}" if user.username else ""
    node_title = escape(key.node.title) if key and key.node else "не задана"
    key_status = '<span class="badge active">active</span>' if key and key.active else '<span class="badge">no key</span>'
    return f"""<tr>
      <td>{subscription.id}</td>
      <td><code>{user.telegram_id}</code><br>{username}</td>
      <td>{escape(subscription.plan_code)}</td>
      <td>{subscription.expires_at:%d.%m.%Y %H:%M UTC}</td>
      <td>{node_title}</td>
      <td>{key_status}</td>
    </tr>"""


def render_private_key_row(key: VpnKey, token_qs: str) -> str:
    user = key.user
    username = f"@{escape(user.username)}" if user and user.username else ""
    telegram_id = user.telegram_id if user else ""
    plan = key.subscription.plan_code if key.subscription else key.key_type
    expires = key.expires_at.strftime("%d.%m.%Y %H:%M UTC") if key.expires_at else "без срока"
    node_title = escape(key.node.title) if key.node else "не задана"
    return f"""<tr>
      <td>{key.id}</td>
      <td><code>{telegram_id}</code><br>{username}</td>
      <td>{escape(plan)}</td>
      <td>{expires}</td>
      <td>{node_title}</td>
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


def percent_text(value: int | None) -> str:
    return f"{value}%" if value is not None else "n/a"
