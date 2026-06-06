import logging

from aiogram import Bot
from aiogram.enums import ParseMode
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db import SessionLocal
from app.models import Order
from app.services.vpn import get_active_key, get_active_subscription
from app.tg.texts import payment_success_text
from app.timeutils import utcnow

logger = logging.getLogger(__name__)


async def notify_paid_orders(bot: Bot, limit: int = 20) -> int:
    sent = 0
    async with SessionLocal() as session:
        order_ids = (
            await session.scalars(
                select(Order.id)
                .where(Order.status == "paid", Order.notified_at.is_(None))
                .order_by(Order.paid_at.asc(), Order.id.asc())
                .limit(limit)
            )
        ).all()

    for order_id in order_ids:
        async with SessionLocal() as session:
            order = await session.scalar(
                select(Order)
                .options(selectinload(Order.user))
                .where(Order.id == order_id, Order.status == "paid", Order.notified_at.is_(None))
                .with_for_update()
            )
            if order is None or order.user is None:
                continue

            subscription = await get_active_subscription(session, order.user_id)
            key = await get_active_key(session, order.user_id)
            try:
                await bot.send_message(
                    order.user.telegram_id,
                    payment_success_text(subscription, key),
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                await session.rollback()
                logger.exception("Failed to notify paid order %s", order.id)
                continue

            order.notified_at = utcnow()
            await session.commit()
            sent += 1

    return sent
