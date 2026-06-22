import logging
from datetime import timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Subscription, SubscriptionReminder, User, UserFeedback
from app.services.vpn import TRIAL_PLAN_CODE
from app.tg import keyboards as kb
from app.tg.texts import trial_feedback_reminder_text, trial_one_day_reminder_text
from app.timeutils import utcnow

logger = logging.getLogger(__name__)

TRIAL_FEEDBACK_REMINDER = "trial_feedback_3d"
TRIAL_EXPIRY_REMINDER = "trial_expiry_1d"
TRIAL_REMINDER_LIMIT = 50


async def send_trial_lifecycle_notifications(session: AsyncSession, bot: Bot, *, limit: int = TRIAL_REMINDER_LIMIT) -> int:
    sent = 0
    sent += await send_trial_feedback_reminders(session, bot, limit=limit)
    sent += await send_trial_expiry_reminders(session, bot, limit=max(0, limit - sent))
    return sent


async def send_trial_feedback_reminders(session: AsyncSession, bot: Bot, *, limit: int) -> int:
    if limit <= 0:
        return 0

    now = utcnow()
    subscriptions = await trial_subscriptions_for_window(
        session,
        reminder_type=TRIAL_FEEDBACK_REMINDER,
        starts_before=now + timedelta(days=3),
        ends_after=now + timedelta(days=1),
        limit=limit,
    )
    sent = 0
    for subscription in subscriptions:
        if await send_subscription_reminder(
            session,
            bot,
            subscription,
            reminder_type=TRIAL_FEEDBACK_REMINDER,
            text=trial_feedback_reminder_text(subscription),
            reply_markup=kb.trial_feedback_keyboard(),
        ):
            sent += 1
    return sent


async def send_trial_expiry_reminders(session: AsyncSession, bot: Bot, *, limit: int) -> int:
    if limit <= 0:
        return 0

    now = utcnow()
    subscriptions = await trial_subscriptions_for_window(
        session,
        reminder_type=TRIAL_EXPIRY_REMINDER,
        starts_before=now + timedelta(days=1),
        ends_after=now,
        limit=limit,
    )
    sent = 0
    for subscription in subscriptions:
        if await send_subscription_reminder(
            session,
            bot,
            subscription,
            reminder_type=TRIAL_EXPIRY_REMINDER,
            text=trial_one_day_reminder_text(subscription),
            reply_markup=kb.trial_expiry_keyboard(),
        ):
            sent += 1
    return sent


async def trial_subscriptions_for_window(
    session: AsyncSession,
    *,
    reminder_type: str,
    starts_before,
    ends_after,
    limit: int,
) -> list[Subscription]:
    now = utcnow()
    candidates = (
        await session.scalars(
            select(Subscription)
            .options(selectinload(Subscription.user))
            .where(
                Subscription.plan_code == TRIAL_PLAN_CODE,
                Subscription.status == "active",
                Subscription.expires_at > ends_after,
                Subscription.expires_at <= starts_before,
                Subscription.expires_at > now,
            )
            .order_by(Subscription.expires_at.asc())
            .limit(limit * 2)
        )
    ).all()

    subscriptions: list[Subscription] = []
    for subscription in candidates:
        existing = await session.scalar(
            select(SubscriptionReminder.id).where(
                SubscriptionReminder.subscription_id == subscription.id,
                SubscriptionReminder.reminder_type == reminder_type,
            )
        )
        if existing is None:
            subscriptions.append(subscription)
        if len(subscriptions) >= limit:
            break
    return subscriptions


async def send_subscription_reminder(
    session: AsyncSession,
    bot: Bot,
    subscription: Subscription,
    *,
    reminder_type: str,
    text: str,
    reply_markup,
) -> bool:
    user = subscription.user
    if user is None:
        user = await session.get(User, subscription.user_id)
    if user is None:
        return False

    try:
        await bot.send_message(user.telegram_id, text, parse_mode="HTML", reply_markup=reply_markup)
    except TelegramForbiddenError:
        logger.warning("Skipping %s reminder for user %s: bot is blocked", reminder_type, user.telegram_id)
        session.add(
            SubscriptionReminder(
                subscription_id=subscription.id,
                user_id=user.id,
                reminder_type=reminder_type,
                sent_at=utcnow(),
                response="delivery_forbidden",
            )
        )
        await session.flush()
        return False
    except Exception:
        logger.exception("Failed to send %s reminder to user %s", reminder_type, user.telegram_id)
        return False

    session.add(
        SubscriptionReminder(
            subscription_id=subscription.id,
            user_id=user.id,
            reminder_type=reminder_type,
            sent_at=utcnow(),
        )
    )
    await session.flush()
    return True


async def record_trial_feedback_response(session: AsyncSession, user: User, response: str) -> UserFeedback | None:
    reminder = await session.scalar(
        select(SubscriptionReminder)
        .where(
            SubscriptionReminder.user_id == user.id,
            SubscriptionReminder.reminder_type == TRIAL_FEEDBACK_REMINDER,
        )
        .order_by(SubscriptionReminder.sent_at.desc())
        .limit(1)
        .with_for_update()
    )
    if reminder is None:
        return None

    reminder.response = response
    reminder.responded_at = utcnow()
    feedback = UserFeedback(
        user_id=user.id,
        subscription_id=reminder.subscription_id,
        reminder_id=reminder.id,
        source="trial_feedback",
        rating=response,
        text=None,
        created_at=utcnow(),
    )
    session.add(feedback)
    await session.flush()
    return feedback


async def complete_custom_feedback_text(
    session: AsyncSession,
    user: User,
    *,
    feedback_id: int | None,
    text: str,
) -> UserFeedback:
    feedback = None
    if feedback_id is not None:
        feedback = await session.scalar(
            select(UserFeedback)
            .where(UserFeedback.id == feedback_id, UserFeedback.user_id == user.id)
            .with_for_update()
        )

    if feedback is None:
        reminder = await latest_trial_feedback_reminder(session, user)
        feedback = UserFeedback(
            user_id=user.id,
            subscription_id=reminder.subscription_id if reminder else None,
            reminder_id=reminder.id if reminder else None,
            source="trial_feedback",
            rating="custom",
            text=None,
            created_at=utcnow(),
        )
        session.add(feedback)

    feedback.rating = feedback.rating or "custom"
    feedback.text = text
    await session.flush()
    return feedback


async def mark_feedback_sent_to_admin(session: AsyncSession, feedback_id: int) -> None:
    feedback = await session.get(UserFeedback, feedback_id)
    if feedback is not None and feedback.sent_to_admin_at is None:
        feedback.sent_to_admin_at = utcnow()
        await session.flush()


async def latest_trial_feedback_reminder(session: AsyncSession, user: User) -> SubscriptionReminder | None:
    return await session.scalar(
        select(SubscriptionReminder)
        .where(
            SubscriptionReminder.user_id == user.id,
            SubscriptionReminder.reminder_type == TRIAL_FEEDBACK_REMINDER,
        )
        .order_by(SubscriptionReminder.sent_at.desc())
        .limit(1)
    )
