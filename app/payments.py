from html import escape
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Order, Plan
from app.services.billing import required_order_amount
from app.services.payment_links import donation_url_for_order, format_amount
from app.timeutils import utcnow

router = APIRouter()


@router.get("/pay/{order_id}", response_class=HTMLResponse)
async def payment_page(
    order_id: int,
    code: str = Query(default=""),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    order = await session.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    require_payment_code(order, code)

    required_amount = await required_order_amount(session, order)
    plan = await session.get(Plan, order.plan_code)
    return HTMLResponse(render_payment_page(order, plan, bool(donation_url_for_order(order, required_amount)), required_amount))


@router.get("/pay/{order_id}/donate")
async def payment_donate(
    order_id: int,
    code: str = Query(default=""),
    email: str = Query(default=""),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    order = await session.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    require_payment_code(order, code)

    donation_url = donation_url_for_order(order, await required_order_amount(session, order), email=clean_email(email))
    if not donation_url:
        raise HTTPException(status_code=400, detail="DonationAlerts URL is not configured")
    return RedirectResponse(donation_url, status_code=303)


def require_payment_code(order: Order, code: str) -> None:
    if not code or not secrets.compare_digest(code, order.payment_code):
        raise HTTPException(status_code=404, detail="Order not found")


def clean_email(value: str) -> str | None:
    email = value.strip()
    if not email:
        return None
    return email[:255]


def render_payment_page(order: Order, plan: Plan | None, can_donate: bool, required_amount) -> str:
    is_active = order.status == "pending" and order.expires_at > utcnow()
    status = "Ожидает оплату" if is_active else f"Статус: {escape(order.status)}"
    plan_title = plan.title if plan is not None else order.plan_code
    amount = format_amount(required_amount)
    donate_button = (
        f"""
        <div class="copy-row">
          <button class="button secondary" type="button" data-copy="{escape(order.payment_code)}">Скопировать код</button>
          <span class="copy-status" aria-live="polite"></span>
        </div>
        <form method="get" action="/pay/{order.id}/donate" target="_blank" rel="noopener">
          <input type="hidden" name="code" value="{escape(order.payment_code)}">
          <label>
            Email для DonationAlerts
            <input name="email" type="email" autocomplete="email" placeholder="mail@example.com">
          </label>
          <button class="button" type="submit" data-open-donation>Скопировать код и открыть оплату</button>
        </form>
        """
        if is_active and can_donate
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
      label {{
        display: grid;
        gap: 7px;
        margin: 12px 0;
        color: #394252;
        font-size: 14px;
      }}
      input {{
        min-height: 42px;
        border: 1px solid #cfd7e2;
        border-radius: 6px;
        padding: 0 12px;
        font: inherit;
      }}
      form {{
        margin-top: 12px;
      }}
      .copy-row {{
        display: flex;
        align-items: center;
        gap: 10px;
        flex-wrap: wrap;
        margin: 0 0 14px;
      }}
      .copy-status {{
        color: #116b35;
        font-size: 13px;
      }}
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
        border: 0;
        border-radius: 6px;
        background: #1563ff;
        color: #fff;
        text-decoration: none;
        font: inherit;
        cursor: pointer;
      }}
      .button.secondary {{
        background: #eef2f6;
        color: #172033;
      }}
      .muted {{ color: #687385; font-size: 13px; }}
    </style>
  </head>
  <body>
    <main>
      <section>
        <h1>Оплата MiloshVPN</h1>
        <p>{status}</p>
        <p>Тариф: <b>{escape(plan_title)}</b></p>
        <p>Проверьте, чтобы сумма была <b>{amount} RUB</b>.</p>
        <p>Сообщение к донату уже подготовлено:</p>
        <code>{escape(order.payment_code)}</code>
        {donate_button}
        <p class="muted" style="margin-top: 16px;">Страница с кодом останется открытой. Если DonationAlerts не подставит сообщение автоматически, вставь скопированный код.</p>
      </section>
    </main>
    <script>
      function copyPaymentCode(value) {{
        if (!value) return;
        if (navigator.clipboard && navigator.clipboard.writeText) {{
          navigator.clipboard.writeText(value).catch(function () {{}});
        }}
      }}

      document.querySelectorAll("[data-copy]").forEach(function (button) {{
        button.addEventListener("click", function () {{
          copyPaymentCode(button.dataset.copy);
          var status = button.parentElement.querySelector(".copy-status");
          if (status) status.textContent = "Скопировано";
        }});
      }});

      document.querySelectorAll("form").forEach(function (form) {{
        form.addEventListener("submit", function () {{
          var code = form.querySelector("input[name='code']");
          copyPaymentCode(code ? code.value : "");
        }});
      }});
    </script>
  </body>
</html>"""
