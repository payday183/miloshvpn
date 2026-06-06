from html import escape
import json
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
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    order = await session.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    require_payment_code(order, code)

    donation_url = donation_url_for_order(order, await required_order_amount(session, order))
    if not donation_url:
        raise HTTPException(status_code=400, detail="DonationAlerts URL is not configured")
    return RedirectResponse(donation_url, status_code=303)


def require_payment_code(order: Order, code: str) -> None:
    if not code or not secrets.compare_digest(code, order.payment_code):
        raise HTTPException(status_code=404, detail="Order not found")


def render_payment_page(order: Order, plan: Plan | None, can_donate: bool, required_amount) -> str:
    is_active = order.status == "pending" and order.expires_at > utcnow()
    status = "Ожидает оплату" if is_active else f"Статус: {escape(order.status)}"
    plan_title = plan.title if plan is not None else order.plan_code
    amount = format_amount(required_amount)
    payment_code = escape(order.payment_code)
    donate_path = f"/pay/{order.id}/donate?code={payment_code}"
    can_open_donation = is_active and can_donate
    donate_link = (
        f'<a class="button" href="{donate_path}" rel="nofollow" data-donation-link>Открыть DonationAlerts</a>'
        if can_open_donation
        else '<p class="muted">Ссылка DonationAlerts не настроена или заказ уже не ожидает оплату.</p>'
    )
    redirect_script = (
        f"""
    <script>
      const paymentCode = {json.dumps(order.payment_code)};
      const donationUrl = {json.dumps(donate_path)};
      const redirectKey = "milosh-pay-opened-" + {json.dumps(order.id)} + "-" + paymentCode;

      function copyPaymentCode() {{
        if (navigator.clipboard && navigator.clipboard.writeText) {{
          return navigator.clipboard.writeText(paymentCode);
        }}
        return Promise.reject();
      }}

      function openDonationAlerts() {{
        const status = document.querySelector("[data-status]");
        const opened = window.sessionStorage && sessionStorage.getItem(redirectKey);
        if (opened) {{
          if (status) status.textContent = "DonationAlerts уже открывался. Введи сумму и вставь код в сообщение.";
          return;
        }}
        if (window.sessionStorage) sessionStorage.setItem(redirectKey, "1");
        copyPaymentCode()
          .then(function () {{
            if (status) status.textContent = "Код скопирован. Открываю DonationAlerts...";
          }})
          .catch(function () {{
            if (status) status.textContent = "Открываю DonationAlerts. Введи сумму и вставь код в сообщение.";
          }})
          .finally(function () {{
            window.setTimeout(function () {{
              window.location.href = donationUrl;
            }}, 550);
          }});
      }}

      window.addEventListener("DOMContentLoaded", function () {{
        const link = document.querySelector("[data-donation-link]");
        if (link) {{
          link.addEventListener("click", function (event) {{
            event.preventDefault();
            copyPaymentCode().finally(function () {{
              window.location.href = donationUrl;
            }});
          }});
        }}
        openDonationAlerts();
      }});
    </script>
        """
        if can_open_donation
        else ""
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
        width: min(100%, 560px);
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
        border: 0;
        border-radius: 6px;
        background: #1563ff;
        color: #fff;
        text-decoration: none;
        font: inherit;
        cursor: pointer;
      }}
      .muted {{ color: #687385; font-size: 13px; }}
      .status {{ color: #116b35; }}
    </style>
  </head>
  <body>
    <main>
      <section>
        <h1>Оплата MiloshVPN</h1>
        <p>{status}</p>
        <p>Тариф: <b>{escape(plan_title)}</b></p>
        <p>Проверьте, чтобы сумма была <b>{amount} RUB</b>.</p>
        <p>Сообщение к донату:</p>
        <code>{payment_code}</code>
        <p class="status" data-status>Открываю DonationAlerts...</p>
        {donate_link}
        <p class="muted" style="margin-top: 16px;">DonationAlerts не подставляет поля из ссылки. Backend засчитает оплату только с этой суммой и этим кодом.</p>
      </section>
    </main>
    {redirect_script}
  </body>
</html>"""
