import asyncio
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
import httpx
from sqlalchemy import func, select

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.models import Order, Plan
from app.services.admin_auth import build_admin_profile_url
from app.services.admin_keys import create_admin_key
from app.services.billing import create_order, poll_donations
from app.services.payment_links import donation_url_for_order
from app.services.public_keys import get_active_public_key, public_key_post_text, rotate_public_key
from app.services.stats import collect_stats
from app.services.users import add_admin, get_or_create_user, is_admin
from app.services.vpn import (
    ensure_trial_subscription,
    get_active_key,
    get_active_subscription,
    list_active_private_keys,
    revoke_private_key,
)
from app.tg import keyboards as kb
from app.tg.texts import (
    admin_help_text,
    admin_key_text,
    instruction_text,
    payment_text,
    policy_text,
    plans_text,
    profile_text,
    start_text,
    subscription_text,
    support_text,
)
from app.timeutils import utcnow

logging.basicConfig(level=logging.INFO)
router = Router()


async def current_user(message: Message):
    async with SessionLocal() as session:
        user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
        )
        await session.commit()
        admin = await is_admin(session, user.telegram_id)
        return user, admin


async def grant_trial_for_start(message: Message) -> bool | None:
    async with SessionLocal() as session:
        user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
        )
        try:
            _, _, created = await ensure_trial_subscription(session, user)
            await session.commit()
            return created
        except Exception:
            await session.rollback()
            logging.exception("Failed to issue trial key for telegram_id=%s", message.from_user.id)
            return None


@router.message(CommandStart())
async def start(message: Message) -> None:
    user, admin = await current_user(message)
    trial_result = await grant_trial_for_start(message)
    await message.answer(
        start_text(user, admin, trial_created=trial_result is True, trial_failed=trial_result is None),
        reply_markup=kb.main_keyboard(admin),
        parse_mode=ParseMode.HTML,
    )


@router.message(F.text == kb.PROFILE)
async def profile(message: Message) -> None:
    user, admin = await current_user(message)
    async with SessionLocal() as session:
        subscription_obj = await get_active_subscription(session, user.id)
        plan = await session.get(Plan, subscription_obj.plan_code) if subscription_obj is not None else None
        key = await get_active_key(session, user.id)
    reply_markup = kb.admin_profile_keyboard(build_admin_profile_url(user.telegram_id)) if admin else kb.main_keyboard(admin)
    await message.answer(profile_text(user, subscription_obj, key, plan), reply_markup=reply_markup, parse_mode=ParseMode.HTML)


@router.message((F.text == kb.SUBSCRIPTION) | (F.text == kb.FREE_KEY))
async def subscription(message: Message) -> None:
    user, admin = await current_user(message)
    async with SessionLocal() as session:
        subscription_obj = await get_active_subscription(session, user.id)
        plan = await session.get(Plan, subscription_obj.plan_code) if subscription_obj is not None else None
        key = await get_active_key(session, user.id)
    await message.answer(
        profile_text(user, subscription_obj, key, plan),
        reply_markup=kb.main_keyboard(admin),
        parse_mode=ParseMode.HTML,
    )


@router.message((F.text == kb.BUY) | (F.text == "Купить пакет") | (F.text == kb.EXTEND))
async def buy(message: Message) -> None:
    await current_user(message)
    async with SessionLocal() as session:
        plans = (await session.scalars(select(Plan).where(Plan.is_active.is_(True)))).all()
    await message.answer(plans_text(list(plans)), reply_markup=kb.plans_keyboard())


@router.callback_query(F.data == "show_plans")
async def show_plans(callback: CallbackQuery) -> None:
    async with SessionLocal() as session:
        plans = (await session.scalars(select(Plan).where(Plan.is_active.is_(True)))).all()
    await callback.message.answer(plans_text(list(plans)), reply_markup=kb.plans_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("buy:"))
async def buy_plan(callback: CallbackQuery) -> None:
    plan_code = callback.data.split(":", 1)[1]
    async with SessionLocal() as session:
        user = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            first_name=callback.from_user.first_name,
        )
        order = await create_order(session, user, plan_code)
        plan = await session.get(Plan, plan_code)
    await callback.message.answer(
        payment_text(order, plan),
        reply_markup=kb.check_payment_keyboard(order.id, donation_url_for_order(order)),
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("check_payment:"))
async def check_payment(callback: CallbackQuery) -> None:
    order_id = int(callback.data.split(":", 1)[1])
    async with SessionLocal() as session:
        user = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            first_name=callback.from_user.first_name,
        )
        await session.commit()
        try:
            await poll_donations(session)
        except httpx.HTTPStatusError as exc:
            logging.warning("DonationAlerts check failed with %s", exc.response.status_code)
        order = await session.get(Order, order_id)
        if order is None:
            await callback.message.answer("Заказ не нашёлся. Лучше создай новый через Купить.")
        elif order.user_id != user.id:
            await callback.message.answer("Этот заказ не твой. У каждого покупателя свой код и своя оплата.")
        elif order.status == "paid":
            subscription_obj = await get_active_subscription(session, user.id)
            key = await get_active_key(session, user.id)
            await callback.message.answer(subscription_text(subscription_obj, key), parse_mode=ParseMode.HTML)
        elif order.status == "pending":
            await callback.message.answer(
                "Пока оплату не вижу. Проверь, что в сообщении DonationAlerts был вот этот код:\n"
                f"<code>{order.payment_code}</code>",
                parse_mode=ParseMode.HTML,
            )
        else:
            await callback.message.answer(f"Статус заказа: {order.status}")
    await callback.answer()


@router.message(F.text == kb.HELP)
async def help_text(message: Message) -> None:
    _, admin = await current_user(message)
    await message.answer(instruction_text(), reply_markup=kb.main_keyboard(admin), parse_mode=ParseMode.HTML)


@router.message(F.text == kb.POLICY)
async def project_policy(message: Message) -> None:
    _, admin = await current_user(message)
    await message.answer(policy_text(), reply_markup=kb.main_keyboard(admin), parse_mode=ParseMode.HTML)


@router.message(F.text == kb.SUPPORT)
async def support(message: Message) -> None:
    _, admin = await current_user(message)
    await message.answer(support_text(), reply_markup=kb.main_keyboard(admin), parse_mode=ParseMode.HTML)


@router.message(F.text == kb.ADMIN)
async def admin_panel(message: Message) -> None:
    user, admin = await current_user(message)
    if not admin:
        await message.answer("Админка только для своих.")
        return
    await message.answer(admin_help_text(), reply_markup=kb.admin_keyboard(), parse_mode=ParseMode.HTML)


@router.message(F.text == kb.ADMIN_MAIN_MENU)
async def back_to_main_menu(message: Message) -> None:
    _, admin = await current_user(message)
    await message.answer("Готово, возвращаю обычное меню.", reply_markup=kb.main_keyboard(admin))


@router.message(Command("admin_key"))
@router.message(F.text == kb.ADMIN_CREATE_KEY)
async def create_admin_key_command(message: Message) -> None:
    _, admin = await current_user(message)
    if not admin:
        return

    async with SessionLocal() as session:
        user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
        )
        key = await create_admin_key(session, user)
        await session.commit()
        await session.refresh(key)

    await message.answer(admin_key_text(key), parse_mode=ParseMode.HTML, reply_markup=kb.admin_keyboard())


@router.message(F.text == kb.ADMIN_KEYS)
async def admin_private_keys(message: Message) -> None:
    _, admin = await current_user(message)
    if not admin:
        return

    async with SessionLocal() as session:
        keys = await list_active_private_keys(session, limit=12)

    if not keys:
        await message.answer("Активных личных ключей пока нет.", reply_markup=kb.admin_keyboard())
        return

    lines = ["🔐 Личные ключи\n"]
    buttons: list[list[InlineKeyboardButton]] = []
    for key in keys:
        user = key.user
        username = f"@{user.username}" if user and user.username else "без username"
        tg_id = user.telegram_id if user else "?"
        expires = key.expires_at.strftime("%d.%m.%Y %H:%M UTC") if key.expires_at else "без срока"
        plan = key.subscription.plan_code if key.subscription else key.key_type
        node = key.node.title if key.node else "нода не задана"
        lines.append(
            f"#{key.id} — {username} / <code>{tg_id}</code>\n"
            f"Тариф: {plan}, до: {expires}\n"
            f"Нода: {node}\n"
        )
        buttons.append([InlineKeyboardButton(text=f"Удалить ключ #{key.id}", callback_data=f"admin_revoke_key:{key.id}")])

    await message.answer(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data.startswith("admin_revoke_key:"))
async def admin_revoke_private_key(callback: CallbackQuery) -> None:
    async with SessionLocal() as session:
        user = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            first_name=callback.from_user.first_name,
        )
        admin = await is_admin(session, user.telegram_id)
        if not admin:
            await callback.answer("Админка закрыта.", show_alert=True)
            return

        key_id = int(callback.data.split(":", 1)[1])
        key = await revoke_private_key(session, key_id)
        await session.commit()

    if key is None:
        await callback.answer("Ключ уже удалён или не найден.", show_alert=True)
        return

    await callback.message.answer(f"Ключ #{key.id} удалён.", reply_markup=kb.admin_keyboard())
    await callback.answer("Удалено")


@router.message(F.text == kb.ADMIN_STATS)
async def admin_stats(message: Message) -> None:
    _, admin = await current_user(message)
    if not admin:
        return
    async with SessionLocal() as session:
        stats = await collect_stats(session)
    await message.answer(
        "Статистика\n\n"
        f"Пользователи: {stats['users']}\n"
        f"Активные подписки: {stats['active_subscriptions']}\n"
        f"Оплаченные заказы: {stats['paid_orders']}\n"
        f"Ожидают оплаты: {stats['pending_orders']}\n"
        f"Активные ключи: {stats['active_keys']}",
        reply_markup=kb.admin_keyboard(),
    )


@router.message(F.text == kb.ADMIN_PENDING)
async def pending_orders(message: Message) -> None:
    _, admin = await current_user(message)
    if not admin:
        return
    async with SessionLocal() as session:
        count = await session.scalar(
            select(func.count()).select_from(Order).where(Order.status == "pending", Order.expires_at > utcnow())
        )
    await message.answer(f"Ожидающих оплат: {int(count or 0)}", reply_markup=kb.admin_keyboard())


@router.message(Command("rotate_free"))
@router.message(F.text == kb.ADMIN_ROTATE_FREE)
async def rotate_free(message: Message) -> None:
    _, admin = await current_user(message)
    if not admin:
        return
    async with SessionLocal() as session:
        key = await rotate_public_key(session)
    await message.answer(public_key_post_text(key), parse_mode=ParseMode.HTML, reply_markup=kb.admin_keyboard())


@router.message(Command("post_free"))
@router.message(F.text == kb.ADMIN_POST_FREE)
async def post_free(message: Message, bot: Bot) -> None:
    _, admin = await current_user(message)
    if not admin:
        return
    settings = get_settings()
    if not settings.public_key_chat_id:
        await message.answer("PUBLIC_KEY_CHAT_ID не задан в .env.", reply_markup=kb.admin_keyboard())
        return
    async with SessionLocal() as session:
        key = await get_active_public_key(session) or await rotate_public_key(session)
    await bot.send_message(settings.public_key_chat_id, public_key_post_text(key), parse_mode=ParseMode.HTML)
    await message.answer("Бесплатный ключ опубликован.", reply_markup=kb.admin_keyboard())


@router.message(Command("add_admin"))
async def add_admin_command(message: Message) -> None:
    _, admin = await current_user(message)
    if not admin:
        return

    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Формат: <code>/add_admin 123456789</code>", parse_mode=ParseMode.HTML)
        return

    async with SessionLocal() as session:
        await add_admin(session, int(parts[1]))
    await message.answer(f"Админ добавлен: {parts[1]}")


async def main() -> None:
    settings = get_settings()
    if not settings.bot_token:
        logging.warning("BOT_TOKEN is empty; Telegram bot is disabled.")
        while True:
            await asyncio.sleep(3600)

    await init_db()
    bot = Bot(token=settings.bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
