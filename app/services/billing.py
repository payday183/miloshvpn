import asyncio
import logging
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
DONATIONALERTS_PAGE_DELAY_SECONDS = 1.0
logger = logging.getLogger(__name__)


async def create_order(session: AsyncSession, user: User, plan_code: str) -> Order:
    plan = await session.get(Plan, plan_code)
    if plan is None or not plan.is_active:
        raise RuntimeError("Тариф недоступен")

    now = utcnow()
    pending_orders = (
        await session.scalars(
            select(Order)
            .where(Order.user_id == user.id, Order.status == "pending", Order.expires_at > now)
            .order_by(Order.created_at.desc())
        )
    ).all()
    reusable_order = next(
        (
            order
            for order in pending_orders
            if order.plan_code == plan.code and Decimal(order.amount_rub) == Decimal(plan.price_rub)
        ),
        None,
    )
    for order in pending_orders:
        if order is not reusable_order:
            order.status = "expired"

    if reusable_order is not None:
        await session.commit()
        await session.refresh(reusable_order)
        return reusable_order

    payment_code = f"MILO-{user.telegram_id}-{secrets.token_hex(3).upper()}"
    order = Order(
        user_id=user.id,
        plan_code=plan.code,
        payment_code=payment_code,
        amount_rub=plan.price_rub,
        status="pending",
        created_at=now,
        expires_at=now + timedelta(hours=12),
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
        try:
            if await process_donation(session, donation):
                await session.commit()
                processed += 1
            else:
                await session.rollback()
        except Exception:
            await session.rollback()
            external_id = donation.get("id") or donation.get("alert_id") or "unknown"
            logger.exception("Donation processing failed for event %s", external_id)
    return processed


async def fetch_donations() -> list[dict[str, Any]]:
    settings = get_settings()
    limit = max(1, min(settings.donationalerts_fetch_limit, 50))
    max_pages = max(1, settings.donationalerts_fetch_pages)
    donations: list[dict[str, Any]] = []
    seen_external_ids: set[str] = set()

    async with httpx.AsyncClient(base_url="https://www.donationalerts.com", timeout=20.0) as client:
        for page in range(1, max_pages + 1):
            if page > 1:
                await asyncio.sleep(DONATIONALERTS_PAGE_DELAY_SECONDS)

            response = await client.get(
                "/api/v1/alerts/donations",
                headers={"Authorization": f"Bearer {settings.donationalerts_token}"},
                params={"limit": limit, "page": page},
            )
            response.raise_for_status()
            payload = response.json()
            page_donations = list(payload.get("data") or [])
            for donation in page_donations:
                external_id = str(donation.get("id") or donation.get("alert_id") or "")
                if external_id and external_id in seen_external_ids:
                    continue
                if external_id:
                    seen_external_ids.add(external_id)
                donations.append(donation)

            meta = payload.get("meta") if isinstance(payload, dict) else None
            links = payload.get("links") if isinstance(payload, dict) else None
            if not page_donations:
                break
            if isinstance(meta, dict):
                current_page = int(meta.get("current_page") or page)
                last_page = int(meta.get("last_page") or current_page)
                if current_page >= last_page:
                    break
            elif isinstance(links, dict) and links.get("next") is None:
                break

    return donations


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
