from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Order, Plan, Subscription, User, VpnKey
from app.services.billing import extract_payment_code
from app.services.vpn import create_or_extend_subscription, create_private_key, get_active_key, get_active_subscription
from app.timeutils import utcnow


class ManualOrderError(RuntimeError):
    pass


@dataclass(frozen=True)
class ManualOrderGrantResult:
    order_id: int
    user_id: int
    user_telegram_id: int
    plan_code: str
    was_already_paid: bool
    granted: bool
    repaired_key: bool
    subscription: Subscription | None
    key: VpnKey | None


async def search_orders_for_admin(session: AsyncSession, query: str, limit: int = 10) -> list[Order]:
    query = query.strip()
    if not query:
        return []

    conditions = []
    payment_code = extract_payment_code(query)
    if payment_code:
        conditions.append(Order.payment_code == payment_code)

    cleaned = query.removeprefix("#").strip()
    if cleaned.isdigit():
        value = int(cleaned)
        conditions.extend(
            [
                Order.id == value,
                Order.user_id == value,
                User.telegram_id == value,
            ]
        )

    username = query.removeprefix("@").strip()
    if username and not payment_code and not cleaned.isdigit():
        conditions.extend(
            [
                Order.payment_code.ilike(f"%{query}%"),
                User.username.ilike(f"%{username}%"),
            ]
        )

    if not conditions:
        return []

    orders = (
        await session.scalars(
            select(Order)
            .join(Order.user)
            .options(selectinload(Order.user), selectinload(Order.plan))
            .where(or_(*conditions))
            .order_by(Order.created_at.desc())
            .limit(limit)
        )
    ).all()
    unique_orders: list[Order] = []
    seen_ids: set[int] = set()
    for order in orders:
        if order.id in seen_ids:
            continue
        seen_ids.add(order.id)
        unique_orders.append(order)
    return unique_orders


async def manually_confirm_order(
    session: AsyncSession,
    order_id: int,
    *,
    admin_telegram_id: int,
) -> ManualOrderGrantResult:
    order = await session.scalar(
        select(Order)
        .options(selectinload(Order.user), selectinload(Order.plan))
        .where(Order.id == order_id)
        .with_for_update()
    )
    if order is None:
        raise ManualOrderError("Заказ не найден.")
    if order.user is None:
        raise ManualOrderError("У заказа не найден пользователь.")

    plan = order.plan or await session.get(Plan, order.plan_code)
    if plan is None:
        raise ManualOrderError("У заказа не найден тариф.")

    user = order.user
    was_already_paid = order.status == "paid"
    repaired_key = False

    if was_already_paid:
        subscription = await get_active_subscription(session, user.id)
        key = await get_active_key(session, user.id)
        if subscription is not None and key is None:
            key = await create_private_key(session, user, subscription)
            repaired_key = True
        return ManualOrderGrantResult(
            order_id=order.id,
            user_id=user.id,
            user_telegram_id=user.telegram_id,
            plan_code=order.plan_code,
            was_already_paid=True,
            granted=False,
            repaired_key=repaired_key,
            subscription=subscription,
            key=key,
        )

    now = utcnow()
    order.status = "paid"
    order.paid_at = now
    order.donation_alert_id = order.donation_alert_id or f"manual:{admin_telegram_id}:{order.id}"

    subscription = await create_or_extend_subscription(session, user, order.plan_code)
    key = await get_active_key(session, user.id)
    return ManualOrderGrantResult(
        order_id=order.id,
        user_id=user.id,
        user_telegram_id=user.telegram_id,
        plan_code=order.plan_code,
        was_already_paid=False,
        granted=True,
        repaired_key=False,
        subscription=subscription,
        key=key,
    )
