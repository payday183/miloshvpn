from html import escape

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Order
from app.services.billing import required_order_amount
from app.services.payment_links import donation_url_for_order, format_amount
from app.timeutils import utcnow

router = APIRouter()


@router.get("/pay/{order_id}", response_class=HTMLResponse)
async def payment_page(order_id: int, session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    order = await session.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")

    required_amount = await required_order_amount(session, order)
    return HTMLResponse(render_payment_page(order, donation_url_for_order(order, required_amount), required_amount))


@router.get("/pay/{order_id}/donate")
async def payment_donate(order_id: int, session: AsyncSession = Depends(get_session)) -> RedirectResponse:
    order = await session.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")

    donation_url = donation_url_for_order(order, await required_order_amount(session, order))
    if not donation_url:
        raise HTTPException(status_code=400, detail="DonationAlerts URL is not configured")
    return RedirectResponse(donation_url, status_code=303)


def render_payment_page(order: Order, donation_url: str, required_amount) -> str:
    is_active = order.status == "pending" and order.expires_at > utcnow()
    status = "Ожидает оплату" if is_active else f"Статус: {escape(order.status)}"
    donate_button = (
        f'<a class="button" href="/pay/{order.id}/donate" rel="noopener">Открыть DonationAlerts</a>'
        if is_active and donation_url
        else '<p class="muted">Ссылка DonationAlerts не настроена или заказ уже не ожидает оплату.</p>'
    )
    return f"""<!doctype html>
<html lang="ru">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Оплата MiloshVPN</title>
    <style>
      :root {{
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        color: #14171d;
        background: #f3f6f9;
      }}
      body {{ margin: 0; }}
      main {{
        min-height: 100vh;
        display: grid;
        place-items: center;
        padding: 24px;
      }}
      section {{
        width: min(100%, 480px);
        background: #fff;
        border: 1px solid #d9e0e8;
        border-radius: 8px;
        padding: 22px;
        box-shadow: 0 14px 38px rgba(20, 28, 38, 0.08);
      }}
      h1 {{
        margin: 0 0 16px;
        font-size: 24px;
        letter-spacing: 0;
      }}
      p {{ margin: 0 0 14px; }}
      code {{
        display: block;
        margin: 8px 0 16px;
        padding: 10px;
        border-radius: 6px;
        background: #eef2f6;
        word-break: break-all;
      }}
      .button {{
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-height: 42px;
        padding: 0 16px;
        border-radius: 6px;
        background: #1563ff;
        color: #fff;
        text-decoration: none;
      }}
      .muted {{ color: #687385; font-size: 13px; }}
    </style>
  </head>
  <body>
    <main>
      <section>
        <h1>Оплата MiloshVPN</h1>
        <p>{status}</p>
        <p>Проверьте, чтобы сумма была <b>{format_amount(required_amount)} RUB</b>.</p>
        <p>Сообщение к донату:</p>
        <code>{escape(order.payment_code)}</code>
        {donate_button}
        <p class="muted" style="margin-top: 16px;">Если DonationAlerts не вставит сообщение автоматически, вставь код из блока выше.</p>
      </section>
    </main>
  </body>
</html>"""
