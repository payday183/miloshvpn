from html import escape
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings, get_settings
from app.db import get_session
from app.models import Order, Subscription, VpnKey, VpnNode
from app.services.billing import poll_donations
from app.services.nodes import activate_node, create_node, disable_node, list_nodes
from app.services.public_keys import rotate_public_key
from app.services.stats import collect_stats
from app.timeutils import utcnow

router = APIRouter()


async def require_admin_token(request: Request) -> None:
    settings = get_settings()
    if not settings.admin_web_token:
        return

    token = request.query_params.get("token") or request.headers.get("X-Admin-Token")
    if token != settings.admin_web_token:
        raise HTTPException(status_code=403, detail="Admin token required")


@router.get("/admin", response_class=HTMLResponse)
async def admin_panel(
    request: Request,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin_token),
) -> HTMLResponse:
    token = request.query_params.get("token", "")
    stats = await collect_stats(session)
    nodes = await list_nodes(session)
    subscriptions = await active_subscriptions(session)
    pending_orders = await latest_pending_orders(session)
    return HTMLResponse(render_admin_page(stats, nodes, subscriptions, pending_orders, token))


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


@router.post("/admin/donations/poll")
async def poll_donations_action(
    request: Request,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin_token),
) -> RedirectResponse:
    await poll_donations(session)
    return redirect_to_admin(request)


@router.post("/admin/public-key/rotate")
async def rotate_public_key_action(
    request: Request,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin_token),
) -> RedirectResponse:
    await rotate_public_key(session)
    return redirect_to_admin(request)


@router.get("/api/admin/nodes")
async def api_nodes(
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin_token),
) -> list[dict[str, object]]:
    return [serialize_node(node) for node in await list_nodes(session)]


@router.get("/api/admin/subscriptions")
async def api_subscriptions(
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin_token),
) -> list[dict[str, object]]:
    rows = await active_subscriptions(session)
    return [serialize_subscription(subscription, key) for subscription, key in rows]


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


def serialize_node(node: VpnNode) -> dict[str, object]:
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


def render_admin_page(
    stats: dict[str, int],
    nodes: list[VpnNode],
    subscriptions: list[tuple[Subscription, VpnKey | None]],
    pending_orders: list[Order],
    token: str,
) -> str:
    token_qs = f"?{urlencode({'token': token})}" if token else ""
    node_rows = "\n".join(render_node_row(node, token_qs) for node in nodes) or table_empty("Нод пока нет")
    sub_rows = "\n".join(render_subscription_row(subscription, key) for subscription, key in subscriptions) or table_empty(
        "Активных подписок пока нет"
    )
    order_rows = "\n".join(render_order_row(order) for order in pending_orders) or table_empty("Ожидающих оплат нет")
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
        <h2>Ноды</h2>
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Название</th>
              <th>Режим</th>
              <th>3x-ui</th>
              <th>VLESS</th>
              <th>Лимит</th>
              <th>Статус</th>
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
        <h2>Ожидающие оплаты</h2>
        <div class="actions" style="margin-bottom: 12px;">
          <form method="post" action="/admin/donations/poll{token_qs}"><button type="submit">Проверить DonationAlerts</button></form>
          <form method="post" action="/admin/public-key/rotate{token_qs}"><button class="secondary" type="submit">Пересоздать free key</button></form>
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
            </tr>
          </thead>
          <tbody>{order_rows}</tbody>
        </table>
      </section>
    </main>
  </body>
</html>"""


def render_stat(label: str, value: int) -> str:
    return f'<div class="stat"><span>{escape(label)}</span><strong>{value}</strong></div>'


def render_node_row(node: VpnNode, token_qs: str) -> str:
    active = '<span class="badge active">active</span>' if node.is_active else '<span class="badge">off</span>'
    action = (
        f'<form method="post" action="/admin/nodes/{node.id}/disable{token_qs}">'
        '<button class="danger" type="submit">Отключить</button></form>'
        if node.is_active
        else f'<form method="post" action="/admin/nodes/{node.id}/activate{token_qs}">'
        '<button type="submit">Активировать</button></form>'
    )
    return f"""<tr>
      <td>{node.id}</td>
      <td>{escape(node.title)}</td>
      <td><span class="badge">{escape(node.mode)}</span></td>
      <td><code>{escape(node.base_url)}</code><br>inbound {node.inbound_id}</td>
      <td><code>{escape(node.public_host)}:{node.public_port}</code></td>
      <td>{node.max_clients}</td>
      <td>{active}</td>
      <td><div class="actions">{action}</div></td>
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


def render_order_row(order: Order) -> str:
    username = f"@{escape(order.user.username)}" if order.user and order.user.username else ""
    return f"""<tr>
      <td>{order.id}</td>
      <td><code>{order.user.telegram_id if order.user else ""}</code><br>{username}</td>
      <td>{escape(order.plan_code)}</td>
      <td>{order.amount_rub} RUB</td>
      <td><code>{escape(order.payment_code)}</code></td>
      <td>{order.expires_at:%d.%m.%Y %H:%M UTC}</td>
    </tr>"""


def table_empty(text: str) -> str:
    return f'<tr><td colspan="8">{escape(text)}</td></tr>'
