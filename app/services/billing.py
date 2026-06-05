import re
import secrets
from datetime import timedelta
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import DonationEvent, Order, Plan, User
from app.services.vpn import create_or_extend_subscription
from app.timeutils import utcnow

CODE_PATTERN = re.compile(r"MILO-[0-9]+-[A-Z0-9]{5,8}")


async def create_order(session: AsyncSession, user: User, plan_code: str) -> Order:
    plan = await session.get(Plan, plan_code)
    if plan is None or not plan.is_active:
        raise RuntimeError("Тариф недоступен")

    payment_code = f"MILO-{user.telegram_id}-{secrets.token_hex(3).upper()}"
    order = Order(
        user_id=user.id,
        plan_code=plan.code,
        payment_code=payment_code,
        amount_rub=plan.price_rub,
        status="pending",
        created_at=utcnow(),
        expires_at=utcnow() + timedelta(hours=12),
    )
    session.add(order)
    await session.commit()
    await session.refresh(order)
    return order


async def get_latest_pending_order(session: AsyncSession, user_id: int) -> Order | None:
    return await session.scalar(
        select(Order)
        .where(Order.user_id == user_id, Order.status == "pending", Order.expires_at > utcnow())
        .order_by(Order.created_at.desc())
    )


async def poll_donations(session: AsyncSession) -> int:
    settings = get_settings()
    if not settings.donationalerts_token:
        return 0

    donations = await fetch_donations()
    processed = 0
    for donation in donations:
        if await process_donation(session, donation):
            processed += 1
    await session.commit()
    return processed


async def fetch_donations() -> list[dict[str, Any]]:
    settings = get_settings()
    async with httpx.AsyncClient(base_url="https://www.donationalerts.com", timeout=20.0) as client:
        response = await client.get(
            "/api/v1/alerts/donations",
            headers={"Authorization": f"Bearer {settings.donationalerts_token}"},
            params={"limit": 50},
        )
        response.raise_for_status()
        payload = response.json()
        return list(payload.get("data") or [])


async def process_donation(session: AsyncSession, donation: dict[str, Any]) -> bool:
    external_id = str(donation.get("id") or donation.get("alert_id") or "")
    if not external_id:
        return False

    existing = await session.scalar(select(DonationEvent).where(DonationEvent.external_id == external_id))
    if existing is not None:
        return False

    message = str(donation.get("message") or "")
    username = str(donation.get("username") or donation.get("name") or "")
    amount = Decimal(str(donation.get("amount") or donation.get("amount_in_user_currency") or "0"))
    currency = str(donation.get("currency") or donation.get("currency_code") or "RUB").upper()

    event = DonationEvent(
        external_id=external_id,
        username=username or None,
        message=message or None,
        amount=amount,
        currency=currency,
        created_at=utcnow(),
        raw=donation,
    )
    session.add(event)

    payment_code = extract_payment_code(message) or extract_payment_code(username)
    if payment_code is None:
        return True

    order = await session.scalar(select(Order).where(Order.payment_code == payment_code, Order.status == "pending"))
    if order is None:
        return True

    if order.expires_at <= utcnow():
        order.status = "expired"
        return True

    if currency not in {"RUB", "RUR"}:
        return True

    if amount < Decimal(order.amount_rub):
        return True

    user = await session.get(User, order.user_id)
    if user is None:
        return True

    order.status = "paid"
    order.paid_at = utcnow()
    order.donation_alert_id = external_id
    await create_or_extend_subscription(session, user, order.plan_code)
    return True


def extract_payment_code(text: str) -> str | None:
    match = CODE_PATTERN.search(text.upper())
    if match is None:
        return None
    return match.group(0)
