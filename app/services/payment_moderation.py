import secrets
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import BotAdmin, Order, Subscription, User, VpnKey
from app.services.payment_modes import MODERATED_PAYMENT_PROVIDERS
from app.services.vpn import create_or_extend_subscription, get_active_key, get_active_subscription
from app.services.x3ui import X3UIClient
from app.timeutils import utcnow

PROVISIONAL_ACCESS_DAYS = 2

PAYMENT_WORDS_LEFT = [
    "крутой",
    "классный",
    "уютный",
    "яркий",
    "добрый",
    "быстрый",
    "спокойный",
    "свежий",
]
PAYMENT_WORDS_RIGHT = [
    "канал",
    "контент",
    "вечер",
    "доступ",
    "проект",
    "интернет",
    "маршрут",
    "сигнал",
]


@dataclass(frozen=True)
class ModerationResult:
    order: Order
    subscription: Subscription | None
    key: VpnKey | None
    changed: bool


def generate_payment_words() -> str:
    return f"{secrets.choice(PAYMENT_WORDS_LEFT)} {secrets.choice(PAYMENT_WORDS_RIGHT)}"


async def ensure_order_note(session: AsyncSession, order: Order) -> str:
    if not order.moderation_note:
        order.moderation_note = generate_payment_words()
        await session.flush()
    return order.moderation_note


async def get_admin_chat_ids(session: AsyncSession) -> list[int]:
    admins = (await session.scalars(select(BotAdmin.telegram_id).order_by(BotAdmin.telegram_id))).all()
    return list(dict.fromkeys(int(admin) for admin in admins))


async def list_pending_review_orders(session: AsyncSession, *, limit: int = 200) -> list[Order]:
    return list(
        (
            await session.scalars(
                select(Order)
                .options(selectinload(Order.user), selectinload(Order.plan))
                .where(
                    Order.status == "provisional",
                    Order.moderation_status == "pending_review",
                    Order.payment_provider.in_(MODERATED_PAYMENT_PROVIDERS),
                )
                .order_by(Order.paid_at.asc(), Order.created_at.asc())
                .limit(limit)
            )
        ).all()
    )


async def grant_provisional_access(session: AsyncSession, order_id: int) -> ModerationResult:
    order = await session.scalar(
        select(Order)
        .options(selectinload(Order.user), selectinload(Order.plan))
        .where(Order.id == order_id)
        .with_for_update()
    )
    if order is None or order.user is None:
        raise RuntimeError("Заказ не найден")
    if order.payment_provider not in MODERATED_PAYMENT_PROVIDERS:
        raise RuntimeError("Ручная проверка доступна только для ручной SBP и гибридной оплаты")

    if order.status == "paid":
        subscription = await get_active_subscription(session, order.user_id)
        key = await get_active_key(session, order.user_id)
        return ModerationResult(order=order, subscription=subscription, key=key, changed=False)

    if order.status == "provisional":
        subscription = await get_active_subscription(session, order.user_id)
        key = await get_active_key(session, order.user_id)
        return ModerationResult(order=order, subscription=subscription, key=key, changed=False)

    await ensure_order_note(session, order)
    order.status = "provisional"
    order.moderation_status = "pending_review"
    order.paid_at = utcnow()
    order.provisional_expires_at = utcnow() + timedelta(days=PROVISIONAL_ACCESS_DAYS)
    subscription = await create_or_extend_subscription(session, order.user, order.plan_code)
    key = await get_active_key(session, order.user_id)
    return ModerationResult(order=order, subscription=subscription, key=key, changed=True)


async def confirm_moderated_order(session: AsyncSession, order_id: int) -> ModerationResult:
    order = await session.scalar(
        select(Order)
        .options(selectinload(Order.user))
        .where(Order.id == order_id)
        .with_for_update()
    )
    if order is None:
        raise RuntimeError("Заказ не найден")
    if order.payment_provider not in MODERATED_PAYMENT_PROVIDERS:
        raise RuntimeError("Этот заказ не относится к ручной или гибридной проверке")

    order.status = "paid"
    order.moderation_status = "confirmed"
    order.provisional_expires_at = None
    order.paid_at = order.paid_at or utcnow()
    subscription = await get_active_subscription(session, order.user_id)
    key = await get_active_key(session, order.user_id)
    return ModerationResult(order=order, subscription=subscription, key=key, changed=True)


async def reject_moderated_order(session: AsyncSession, order_id: int) -> ModerationResult:
    order = await session.scalar(
        select(Order)
        .options(selectinload(Order.user))
        .where(Order.id == order_id)
        .with_for_update()
    )
    if order is None:
        raise RuntimeError("Заказ не найден")
    if order.payment_provider not in MODERATED_PAYMENT_PROVIDERS:
        raise RuntimeError("Этот заказ не относится к ручной или гибридной проверке")

    await revoke_user_access(session, order.user_id)
    order.status = "rejected"
    order.moderation_status = "rejected"
    order.provisional_expires_at = None
    subscription = await get_active_subscription(session, order.user_id)
    key = await get_active_key(session, order.user_id)
    return ModerationResult(order=order, subscription=subscription, key=key, changed=True)


async def revoke_user_access(session: AsyncSession, user_id: int) -> None:
    now = utcnow()
    subscriptions = (
        await session.scalars(
            select(Subscription)
            .where(Subscription.user_id == user_id, Subscription.status == "active")
            .order_by(Subscription.expires_at.desc())
        )
    ).all()
    for subscription in subscriptions:
        subscription.status = "rejected"

    keys = (
        await session.scalars(
            select(VpnKey)
            .options(selectinload(VpnKey.node))
            .where(VpnKey.user_id == user_id, VpnKey.key_type == "private", VpnKey.active.is_(True))
        )
    ).all()
    for key in keys:
        try:
            await X3UIClient(node=key.node).revoke_client(client_uuid=key.x3ui_client_uuid, email=key.email)
        except Exception:
            continue
        key.active = False
        key.revoked_at = now


async def expire_unconfirmed_orders(session: AsyncSession, *, limit: int = 100) -> int:
    orders = (
        await session.scalars(
            select(Order)
            .where(
                Order.status == "provisional",
                Order.moderation_status == "pending_review",
                Order.payment_provider.in_(MODERATED_PAYMENT_PROVIDERS),
                Order.provisional_expires_at <= utcnow(),
            )
            .order_by(Order.provisional_expires_at.asc())
            .limit(limit)
        )
    ).all()

    expired = 0
    for order in orders:
        await reject_moderated_order(session, order.id)
        expired += 1
    await session.commit()
    return expired
