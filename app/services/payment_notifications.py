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
        if await notify_paid_order(bot, order_id):
            sent += 1

    return sent


async def notify_paid_order(bot: Bot, order_id: int, *, force: bool = False) -> bool:
    async with SessionLocal() as session:
        query = (
            select(Order)
            .options(selectinload(Order.user))
            .where(Order.id == order_id, Order.status == "paid")
            .with_for_update()
        )
        if not force:
            query = query.where(Order.notified_at.is_(None))

        order = await session.scalar(query)
        if order is None or order.user is None:
            return False

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
            return False

        order.notified_at = utcnow()
        await session.commit()
        return True
