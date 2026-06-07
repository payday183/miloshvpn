import asyncio
import logging
import re

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import BaseFilter, Command, CommandStart
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.models import Order, Plan
from app.services.admin_auth import build_admin_profile_url
from app.services.admin_keys import create_admin_key
from app.services.billing import create_order, poll_donations
from app.services.manual_orders import ManualOrderError, manually_confirm_order, search_orders_for_admin
from app.services.payment_modes import (
    PAYMENT_PROVIDER_DONATIONALERTS,
    PAYMENT_PROVIDER_HYBRID,
    PAYMENT_PROVIDER_MANUAL_SBP,
    get_active_payment_provider,
    set_active_payment_provider,
)
from app.services.payment_moderation import (
    confirm_moderated_order,
    ensure_order_note,
    get_admin_chat_ids,
    grant_provisional_access,
    list_pending_review_orders,
    reject_moderated_order,
)
from app.services.payment_links import donation_url_for_order
from app.services.payment_notifications import notify_paid_order, notify_paid_orders
from app.services.public_keys import (
    get_active_public_key,
    mark_public_key_posted,
    normalize_public_key_chat_id,
    public_key_channel_post_text,
    public_key_post_text,
    rotate_public_key,
)
from app.services.seed import ADMIN_TEST_PLAN_CODE
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
    admin_manual_grant_result_text,
    admin_qr_request_text,
    admin_order_result_text,
    admin_order_search_prompt_text,
    admin_review_fast_prompt_text,
    admin_review_fast_result_text,
    admin_review_orders_menu_text,
    admin_review_orders_txt,
    admin_review_queue_item_text,
    admin_review_request_text,
    hybrid_payment_text,
    instruction_text,
    manual_sbp_payment_text,
    manual_sbp_qr_requested_text,
    moderation_rejected_user_text,
    payment_mode_admin_text,
    payment_mode_applied_text,
    payment_mode_confirm_text,
    payment_text,
    payment_success_text,
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
ADMIN_ORDER_QUERY_RE = re.compile(r"(MILO-[0-9]+-[A-Z0-9]{5,8}|#?[0-9]{1,20})", re.IGNORECASE)
PENDING_QR_UPLOADS: dict[int, int] = {}
PENDING_FAST_REVIEW_UPLOADS: set[int] = set()
REVIEW_QUEUE_STATE: dict[int, list[int]] = {}
REVIEW_QUEUE_TOTALS: dict[int, int] = {}


class PendingFastReviewFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return bool(message.from_user and message.from_user.id in PENDING_FAST_REVIEW_UPLOADS)


def visible_purchase_plans(plans: list[Plan], admin: bool) -> list[Plan]:
    if admin:
        return plans
    return [plan for plan in plans if plan.code != ADMIN_TEST_PLAN_CODE]


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
    _, admin = await current_user(message)
    async with SessionLocal() as session:
        plans = (
            await session.scalars(select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.price_rub.asc()))
        ).all()
    visible_plans = visible_purchase_plans(list(plans), admin)
    await message.answer(
        plans_text(visible_plans),
        reply_markup=kb.plans_keyboard(include_admin_test=admin),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == "show_plans")
async def show_plans(callback: CallbackQuery) -> None:
    async with SessionLocal() as session:
        user = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            first_name=callback.from_user.first_name,
        )
        admin = await is_admin(session, user.telegram_id)
        plans = (
            await session.scalars(select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.price_rub.asc()))
        ).all()
        await session.commit()
    visible_plans = visible_purchase_plans(list(plans), admin)
    await callback.message.answer(
        plans_text(visible_plans),
        reply_markup=kb.plans_keyboard(include_admin_test=admin),
        parse_mode=ParseMode.HTML,
    )
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
        admin = await is_admin(session, user.telegram_id)
        if plan_code == ADMIN_TEST_PLAN_CODE and not admin:
            await session.commit()
            await callback.answer("Тестовый тариф доступен только админам.", show_alert=True)
            return
        plan = await session.get(Plan, plan_code)
        if plan is None or not plan.is_active:
            await session.commit()
            await callback.answer("Тариф недоступен.", show_alert=True)
            return
        order = await create_order(session, user, plan_code)
    if order.payment_provider == PAYMENT_PROVIDER_MANUAL_SBP:
        await callback.message.answer(
            manual_sbp_payment_text(order, plan),
            reply_markup=kb.manual_sbp_keyboard(order.id),
            parse_mode=ParseMode.HTML,
        )
    elif order.payment_provider == PAYMENT_PROVIDER_HYBRID:
        await callback.message.answer(
            hybrid_payment_text(order, plan),
            reply_markup=kb.check_payment_keyboard(order.id, donation_url_for_order(order)),
            parse_mode=ParseMode.HTML,
        )
    else:
        await callback.message.answer(
            payment_text(order, plan),
            reply_markup=kb.check_payment_keyboard(order.id, donation_url_for_order(order)),
            parse_mode=ParseMode.HTML,
        )
    await callback.answer()


@router.callback_query(F.data.startswith("manual_sbp:"))
async def manual_sbp_selected(callback: CallbackQuery, bot: Bot) -> None:
    order_id = int(callback.data.split(":", 1)[1])
    async with SessionLocal() as session:
        user = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            first_name=callback.from_user.first_name,
        )
        order = await session.scalar(
            select(Order)
            .options(selectinload(Order.user), selectinload(Order.plan))
            .where(Order.id == order_id)
            .with_for_update()
        )
        if order is None or order.user_id != user.id:
            await session.commit()
            await callback.answer("Заказ не найден.", show_alert=True)
            return
        await ensure_order_note(session, order)
        order.moderation_status = "qr_requested"
        await session.commit()

    await callback.message.answer(manual_sbp_qr_requested_text(order), parse_mode=ParseMode.HTML)
    await send_admin_order_message(
        bot,
        admin_qr_request_text(order),
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Отправить QR code", callback_data=f"admin_qr_upload:{order.id}")]
            ]
        ),
    )
    await callback.answer("СБП выбран")


@router.callback_query(F.data.startswith("check_payment:"))
async def check_payment(callback: CallbackQuery, bot: Bot) -> None:
    order_id = int(callback.data.split(":", 1)[1])
    async with SessionLocal() as session:
        user = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            first_name=callback.from_user.first_name,
        )
        order = await session.get(Order, order_id)
        if order is None:
            await callback.message.answer("Заказ не нашёлся. Лучше создай новый через Купить.")
        elif order.user_id != user.id:
            await callback.message.answer("Этот заказ не твой. У каждого покупателя свой код и своя оплата.")
        elif order.status == "paid":
            subscription_obj = await get_active_subscription(session, user.id)
            key = await get_active_key(session, user.id)
            order.notified_at = order.notified_at or utcnow()
            await session.commit()
            await callback.message.answer(payment_success_text(subscription_obj, key), parse_mode=ParseMode.HTML)
        elif order.status == "provisional":
            subscription_obj = await get_active_subscription(session, user.id)
            key = await get_active_key(session, user.id)
            await session.commit()
            await callback.message.answer(payment_success_text(subscription_obj, key), parse_mode=ParseMode.HTML)
        elif order.status == "pending":
            if order.payment_provider in {PAYMENT_PROVIDER_MANUAL_SBP, PAYMENT_PROVIDER_HYBRID}:
                await session.commit()
                async with SessionLocal() as grant_session:
                    result = await grant_provisional_access(grant_session, order.id)
                    await grant_session.commit()
                await callback.message.answer(
                    "⏳ Проверка запущена.\n\n"
                    "Спасибо за поддержку. Админ сверит оплату, а доступ уже готов.",
                    parse_mode=ParseMode.HTML,
                )
                await callback.message.answer(
                    payment_success_text(result.subscription, result.key),
                    parse_mode=ParseMode.HTML,
                )
                await send_admin_order_message(
                    bot,
                    admin_review_request_text(result.order),
                    admin_review_keyboard(result.order.id),
                )
                await callback.answer("Проверка запущена")
                return

            await session.commit()
            await callback.message.answer(
                "⏳ Ваша оплата проверяется.\n\n"
                "Подождите пару минут, DonationAlerts иногда отдаёт донат не сразу.\n\n"
                "Когда backend увидит оплату, бот автоматически пришлёт вам ключ и подписка появится в Профиле.\n\n"
                "Если произошла ошибка или ключ не пришёл через пару минут, напишите админу @miloshadmin — поможем.\n\n"
                "Проверьте, что в сообщении DonationAlerts был этот код:\n"
                f"<code>{order.payment_code}</code>",
                parse_mode=ParseMode.HTML,
            )
            await callback.answer("Проверяем оплату")
            try:
                async with SessionLocal() as poll_session:
                    await poll_donations(poll_session)
                await notify_paid_orders(bot)
            except httpx.HTTPStatusError as exc:
                logging.warning("DonationAlerts check failed with %s", exc.response.status_code)
                await callback.message.answer(
                    "⚠️ Проверка оплаты сейчас споткнулась.\n\n"
                    "Не переживайте: если донат был с правильным кодом, мы поможем его найти. "
                    "Напишите админу @miloshadmin."
                )
            except Exception:
                logging.exception("Payment check failed")
                await callback.message.answer(
                    "⚠️ Проверка оплаты сейчас споткнулась.\n\n"
                    "Не переживайте: если донат был с правильным кодом, мы поможем его найти. "
                    "Напишите админу @miloshadmin."
                )
            return
        else:
            await session.commit()
            await callback.message.answer(f"Статус заказа: {order.status}")
    await callback.answer()


def admin_review_keyboard(order_id: int, *, prefix: str = "admin_review") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Оплатил", callback_data=f"{prefix}_confirm:{order_id}"),
                InlineKeyboardButton(text="❌ Не оплатил", callback_data=f"{prefix}_reject:{order_id}"),
            ]
        ]
    )


def admin_review_orders_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Очередь", callback_data="admin_review_queue")],
            [InlineKeyboardButton(text="Сделать файл txt", callback_data="admin_review_txt")],
            [InlineKeyboardButton(text="Весь очередный список", callback_data="admin_review_all")],
            [InlineKeyboardButton(text="Быстрая проверка", callback_data="admin_review_fast")],
        ]
    )


async def send_admin_order_message(bot: Bot, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    async with SessionLocal() as session:
        admin_ids = await get_admin_chat_ids(session)
    for admin_id in admin_ids:
        try:
            await bot.send_message(admin_id, text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
        except Exception:
            logging.exception("Failed to send admin moderation message to %s", admin_id)


async def callback_from_admin(callback: CallbackQuery) -> bool:
    async with SessionLocal() as session:
        user = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            first_name=callback.from_user.first_name,
        )
        admin = await is_admin(session, user.telegram_id)
        await session.commit()
    return admin


async def apply_admin_review_decision(bot: Bot, order_id: int, *, approved: bool) -> int | None:
    async with SessionLocal() as session:
        result = (
            await confirm_moderated_order(session, order_id)
            if approved
            else await reject_moderated_order(session, order_id)
        )
        user_telegram_id = result.order.user.telegram_id if result.order.user else None
        await session.commit()

    if user_telegram_id is None:
        return None

    if approved:
        await bot.send_message(user_telegram_id, "✅ Оплата подтверждена. Тариф закреплён, спасибо за поддержку!")
    else:
        await bot.send_message(user_telegram_id, moderation_rejected_user_text())
    return user_telegram_id


async def send_next_review_queue_item(message: Message, admin_id: int) -> None:
    order_ids = REVIEW_QUEUE_STATE.get(admin_id, [])
    total = REVIEW_QUEUE_TOTALS.get(admin_id, len(order_ids))

    while order_ids:
        order_id = order_ids[0]
        async with SessionLocal() as session:
            order = await session.scalar(
                select(Order)
                .options(selectinload(Order.user), selectinload(Order.plan))
                .where(Order.id == order_id)
            )
        if order and order.status == "provisional" and order.moderation_status == "pending_review":
            position = total - len(order_ids) + 1
            await message.answer(
                admin_review_queue_item_text(order, position, total),
                parse_mode=ParseMode.HTML,
                reply_markup=admin_review_keyboard(order.id, prefix="admin_queue"),
            )
            return
        order_ids.pop(0)

    REVIEW_QUEUE_STATE.pop(admin_id, None)
    REVIEW_QUEUE_TOTALS.pop(admin_id, None)
    await message.answer("✅ Очередь закончилась. Все заказы из этого прохода разобраны.", reply_markup=kb.admin_keyboard())


def normalize_review_token(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().strip("`")).lower()


def parse_review_tokens(raw: str) -> set[str]:
    tokens: set[str] = set()
    for line in raw.splitlines():
        text = line.replace("<code>", "").replace("</code>", "").strip()
        if not text or set(text) <= {"-", "—", "_"}:
            continue
        code_matches = re.findall(r"MILO-[0-9]+-[A-Z0-9]{5,8}", text, flags=re.IGNORECASE)
        if code_matches:
            tokens.update(normalize_review_token(match) for match in code_matches)
        else:
            tokens.add(normalize_review_token(text))
    return {token for token in tokens if token}


def order_review_tokens(order: Order) -> set[str]:
    tokens = {normalize_review_token(order.payment_code)}
    if order.moderation_note:
        tokens.add(normalize_review_token(order.moderation_note))
    return tokens


async def read_fast_review_payload(message: Message, bot: Bot) -> str:
    if message.text:
        return message.text
    if not message.document:
        return ""
    if message.document.file_size and message.document.file_size > 1024 * 1024:
        raise RuntimeError("Файл слишком большой. Пришлите txt до 1 МБ.")

    file = await bot.get_file(message.document.file_id)
    if not file.file_path:
        raise RuntimeError("Не получилось скачать файл.")
    stream = await bot.download_file(file.file_path)
    data = stream.read()
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


async def run_fast_review(message: Message, bot: Bot, raw_payload: str) -> None:
    tokens = parse_review_tokens(raw_payload)
    if not tokens:
        await message.answer(
            "Не увидел кодов в файле или сообщении.\n\nКаждый подтверждённый код отправляйте с новой строки.",
            reply_markup=kb.admin_keyboard(),
        )
        return

    confirmed_users: list[int] = []
    rejected_users: list[int] = []
    matched_tokens: set[str] = set()
    async with SessionLocal() as session:
        orders = await list_pending_review_orders(session, limit=500)
        for order in orders:
            order_tokens = order_review_tokens(order)
            matched = bool(order_tokens & tokens)
            if matched:
                result = await confirm_moderated_order(session, order.id)
                if result.order.user:
                    confirmed_users.append(result.order.user.telegram_id)
                matched_tokens.update(order_tokens & tokens)
            else:
                result = await reject_moderated_order(session, order.id)
                if result.order.user:
                    rejected_users.append(result.order.user.telegram_id)
        await session.commit()

    for telegram_id in confirmed_users:
        try:
            await bot.send_message(telegram_id, "✅ Оплата подтверждена. Тариф закреплён, спасибо за поддержку!")
        except Exception:
            logging.exception("Failed to notify confirmed user %s", telegram_id)

    for telegram_id in rejected_users:
        try:
            await bot.send_message(telegram_id, moderation_rejected_user_text())
        except Exception:
            logging.exception("Failed to notify rejected user %s", telegram_id)

    unknown = sorted(tokens - matched_tokens)
    await message.answer(
        admin_review_fast_result_text(len(confirmed_users), len(rejected_users), unknown),
        parse_mode=ParseMode.HTML,
        reply_markup=kb.admin_keyboard(),
    )


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


@router.message(F.text == kb.ADMIN_REVIEW_ORDERS)
async def admin_review_orders_menu(message: Message) -> None:
    _, admin = await current_user(message)
    if not admin:
        return
    async with SessionLocal() as session:
        orders = await list_pending_review_orders(session, limit=500)
    await message.answer(
        admin_review_orders_menu_text(len(orders)),
        parse_mode=ParseMode.HTML,
        reply_markup=admin_review_orders_keyboard(),
    )


@router.callback_query(F.data == "admin_review_queue")
async def admin_review_queue_start(callback: CallbackQuery) -> None:
    if not await callback_from_admin(callback):
        await callback.answer("Админка закрыта.", show_alert=True)
        return

    async with SessionLocal() as session:
        orders = await list_pending_review_orders(session, limit=500)
    if not orders:
        await callback.message.answer("✅ Очередь чистая, проверять сейчас нечего.", reply_markup=kb.admin_keyboard())
        await callback.answer("Пусто")
        return

    REVIEW_QUEUE_STATE[callback.from_user.id] = [order.id for order in orders]
    REVIEW_QUEUE_TOTALS[callback.from_user.id] = len(orders)
    await callback.answer("Очередь запущена")
    await send_next_review_queue_item(callback.message, callback.from_user.id)


@router.callback_query(F.data == "admin_review_txt")
async def admin_review_txt(callback: CallbackQuery) -> None:
    if not await callback_from_admin(callback):
        await callback.answer("Админка закрыта.", show_alert=True)
        return

    async with SessionLocal() as session:
        orders = await list_pending_review_orders(session, limit=500)
    content = admin_review_orders_txt(orders)
    document = BufferedInputFile(content.encode("utf-8"), filename="miloshvpn_review_orders.txt")
    await callback.message.answer_document(
        document,
        caption=f"TXT по очереди проверки: {len(orders)} заказов.",
        reply_markup=kb.admin_keyboard(),
    )
    await callback.answer("Файл готов")


@router.callback_query(F.data == "admin_review_all")
async def admin_review_all(callback: CallbackQuery) -> None:
    if not await callback_from_admin(callback):
        await callback.answer("Админка закрыта.", show_alert=True)
        return

    async with SessionLocal() as session:
        orders = await list_pending_review_orders(session, limit=500)
    if not orders:
        await callback.message.answer("✅ Очередь чистая, проверять сейчас нечего.", reply_markup=kb.admin_keyboard())
        await callback.answer("Пусто")
        return

    await callback.answer("Отправляю список")
    await callback.message.answer(f"Отправляю весь список: {len(orders)} заказов.", reply_markup=kb.admin_keyboard())
    for order in orders:
        await callback.message.answer(
            admin_review_request_text(order),
            parse_mode=ParseMode.HTML,
            reply_markup=admin_review_keyboard(order.id),
        )
        await asyncio.sleep(0.05)


@router.callback_query(F.data == "admin_review_fast")
async def admin_review_fast(callback: CallbackQuery) -> None:
    if not await callback_from_admin(callback):
        await callback.answer("Админка закрыта.", show_alert=True)
        return

    async with SessionLocal() as session:
        orders = await list_pending_review_orders(session, limit=500)
    if not orders:
        await callback.message.answer("✅ Очередь чистая, быстрый список сейчас не нужен.", reply_markup=kb.admin_keyboard())
        await callback.answer("Пусто")
        return

    PENDING_FAST_REVIEW_UPLOADS.add(callback.from_user.id)
    await callback.message.answer(
        admin_review_fast_prompt_text(len(orders)),
        parse_mode=ParseMode.HTML,
        reply_markup=kb.admin_keyboard(),
    )
    await callback.answer("Жду txt")


@router.message(F.text == kb.ADMIN_PAYMENT_MODE)
async def admin_payment_mode(message: Message) -> None:
    _, admin = await current_user(message)
    if not admin:
        return
    async with SessionLocal() as session:
        provider = await get_active_payment_provider(session)
    await message.answer(
        payment_mode_admin_text(provider),
        reply_markup=kb.payment_provider_keyboard(),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data.startswith("admin_paymode_select:"))
async def admin_payment_mode_select(callback: CallbackQuery) -> None:
    async with SessionLocal() as session:
        user = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            first_name=callback.from_user.first_name,
        )
        admin = await is_admin(session, user.telegram_id)
        await session.commit()
    if not admin:
        await callback.answer("Админка закрыта.", show_alert=True)
        return

    provider = callback.data.split(":", 1)[1]
    await callback.message.answer(
        payment_mode_confirm_text(provider),
        reply_markup=kb.confirm_payment_provider_keyboard(provider),
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_paymode_confirm:"))
async def admin_payment_mode_confirm(callback: CallbackQuery) -> None:
    provider = callback.data.split(":", 1)[1]
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
        try:
            await set_active_payment_provider(session, provider)
            await session.commit()
        except ValueError:
            await session.rollback()
            await callback.answer("Неизвестная система оплаты.", show_alert=True)
            return

    await callback.message.answer(payment_mode_applied_text(provider), parse_mode=ParseMode.HTML, reply_markup=kb.admin_keyboard())
    await callback.answer("Сохранено")


@router.callback_query(F.data == "admin_paymode_cancel")
async def admin_payment_mode_cancel(callback: CallbackQuery) -> None:
    await callback.answer("Отменено")
    await callback.message.answer("Ок, систему оплаты не меняю.", reply_markup=kb.admin_keyboard())


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


@router.callback_query(F.data.startswith("admin_qr_upload:"))
async def admin_qr_upload_start(callback: CallbackQuery) -> None:
    async with SessionLocal() as session:
        user = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            first_name=callback.from_user.first_name,
        )
        admin = await is_admin(session, user.telegram_id)
        await session.commit()
    if not admin:
        await callback.answer("Админка закрыта.", show_alert=True)
        return

    order_id = int(callback.data.split(":", 1)[1])
    PENDING_QR_UPLOADS[callback.from_user.id] = order_id
    await callback.message.answer(
        f"Отправьте QR code картинкой или файлом для заказа <code>{order_id}</code>.",
        parse_mode=ParseMode.HTML,
        reply_markup=kb.admin_keyboard(),
    )
    await callback.answer("Жду QR")


@router.message(PendingFastReviewFilter(), F.text | F.document)
async def admin_fast_review_receive(message: Message, bot: Bot) -> None:
    _, admin = await current_user(message)
    if not admin:
        PENDING_FAST_REVIEW_UPLOADS.discard(message.from_user.id)
        return

    try:
        payload = await read_fast_review_payload(message, bot)
    except RuntimeError as exc:
        await message.answer(str(exc), reply_markup=kb.admin_keyboard())
        return
    except Exception:
        logging.exception("Failed to read fast review payload")
        await message.answer("Не получилось прочитать txt-файл. Попробуйте отправить коды обычным сообщением.")
        return

    PENDING_FAST_REVIEW_UPLOADS.discard(message.from_user.id)
    await run_fast_review(message, bot, payload)


@router.message(F.photo | F.document)
async def admin_qr_upload_receive(message: Message, bot: Bot) -> None:
    order_id = PENDING_QR_UPLOADS.get(message.from_user.id)
    if order_id is None:
        return

    async with SessionLocal() as session:
        admin_user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
        )
        admin = await is_admin(session, admin_user.telegram_id)
        order = await session.scalar(
            select(Order)
            .options(selectinload(Order.user))
            .where(Order.id == order_id)
            .with_for_update()
        )
        if not admin or order is None or order.user is None:
            await session.commit()
            PENDING_QR_UPLOADS.pop(message.from_user.id, None)
            return

        file_id = message.photo[-1].file_id if message.photo else message.document.file_id
        order.manual_qr_file_id = file_id
        order.moderation_status = "qr_sent"
        await session.commit()

    caption = (
        "QR-код СБП для оплаты готов.\n\n"
        "После оплаты нажмите <b>Проверить оплату</b> в сообщении заказа."
    )
    try:
        if message.photo:
            await bot.send_photo(order.user.telegram_id, file_id, caption=caption, parse_mode=ParseMode.HTML)
        else:
            await bot.send_document(order.user.telegram_id, file_id, caption=caption, parse_mode=ParseMode.HTML)
        await message.answer(
            f"QR code доставлен пользователю <code>{order.user.telegram_id}</code>.",
            parse_mode=ParseMode.HTML,
            reply_markup=kb.admin_keyboard(),
        )
    finally:
        PENDING_QR_UPLOADS.pop(message.from_user.id, None)


@router.callback_query(F.data.startswith("admin_review_confirm:"))
async def admin_review_confirm(callback: CallbackQuery, bot: Bot) -> None:
    order_id = int(callback.data.split(":", 1)[1])
    if not await callback_from_admin(callback):
        await callback.answer("Админка закрыта.", show_alert=True)
        return
    try:
        await apply_admin_review_decision(bot, order_id, approved=True)
    except RuntimeError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer(
        f"Заказ <code>{order_id}</code> подтверждён.",
        parse_mode=ParseMode.HTML,
        reply_markup=kb.admin_keyboard(),
    )
    await callback.answer("Подтверждено")


@router.callback_query(F.data.startswith("admin_review_reject:"))
async def admin_review_reject(callback: CallbackQuery, bot: Bot) -> None:
    order_id = int(callback.data.split(":", 1)[1])
    if not await callback_from_admin(callback):
        await callback.answer("Админка закрыта.", show_alert=True)
        return
    try:
        await apply_admin_review_decision(bot, order_id, approved=False)
    except RuntimeError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer(
        f"Заказ <code>{order_id}</code> отклонён, доступ отозван.",
        parse_mode=ParseMode.HTML,
        reply_markup=kb.admin_keyboard(),
    )
    await callback.answer("Отклонено")


@router.callback_query(F.data.startswith("admin_queue_confirm:"))
async def admin_queue_confirm(callback: CallbackQuery, bot: Bot) -> None:
    await handle_admin_queue_decision(callback, bot, approved=True)


@router.callback_query(F.data.startswith("admin_queue_reject:"))
async def admin_queue_reject(callback: CallbackQuery, bot: Bot) -> None:
    await handle_admin_queue_decision(callback, bot, approved=False)


async def handle_admin_queue_decision(callback: CallbackQuery, bot: Bot, *, approved: bool) -> None:
    order_id = int(callback.data.split(":", 1)[1])
    if not await callback_from_admin(callback):
        await callback.answer("Админка закрыта.", show_alert=True)
        return
    try:
        await apply_admin_review_decision(bot, order_id, approved=approved)
    except RuntimeError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    order_ids = REVIEW_QUEUE_STATE.get(callback.from_user.id, [])
    if order_id in order_ids:
        order_ids.remove(order_id)

    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer("Подтверждено" if approved else "Отклонено")
    await send_next_review_queue_item(callback.message, callback.from_user.id)


@router.message(Command("find_order"))
async def admin_find_order_command(message: Message) -> None:
    _, admin = await current_user(message)
    if not admin:
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip():
        await message.answer(admin_order_search_prompt_text(), parse_mode=ParseMode.HTML, reply_markup=kb.admin_keyboard())
        return

    await send_admin_order_search_results(message, parts[1].strip())


@router.message(F.text == kb.ADMIN_FIND_ORDER)
async def admin_find_order_prompt(message: Message) -> None:
    _, admin = await current_user(message)
    if not admin:
        return
    await message.answer(admin_order_search_prompt_text(), parse_mode=ParseMode.HTML, reply_markup=kb.admin_keyboard())


@router.message(F.text.func(lambda text: bool(text and ADMIN_ORDER_QUERY_RE.fullmatch(text.strip()))))
async def admin_find_order_by_plain_text(message: Message) -> None:
    _, admin = await current_user(message)
    if not admin:
        return
    await send_admin_order_search_results(message, (message.text or "").strip())


async def send_admin_order_search_results(message: Message, query: str) -> None:
    async with SessionLocal() as session:
        orders = await search_orders_for_admin(session, query, limit=5)

    if not orders:
        await message.answer(
            "Ничего не нашёл по этому коду или ID.\n\n"
            "Проверь, что код полностью совпадает с тем, что пользователь вставлял в DonationAlerts.",
            reply_markup=kb.admin_keyboard(),
        )
        return

    for order in orders:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Выдать ключ пользователю", callback_data=f"admin_grant_order:{order.id}")],
            ]
        )
        await message.answer(admin_order_result_text(order), reply_markup=keyboard, parse_mode=ParseMode.HTML)


@router.callback_query(F.data.startswith("admin_grant_order:"))
async def admin_grant_order(callback: CallbackQuery, bot: Bot) -> None:
    async with SessionLocal() as session:
        admin_user = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            first_name=callback.from_user.first_name,
        )
        admin = await is_admin(session, admin_user.telegram_id)
        if not admin:
            await callback.answer("Админка закрыта.", show_alert=True)
            return

        order_id = int(callback.data.split(":", 1)[1])
        try:
            result = await manually_confirm_order(session, order_id, admin_telegram_id=admin_user.telegram_id)
            await session.commit()
        except ManualOrderError as exc:
            await session.rollback()
            await callback.answer(str(exc), show_alert=True)
            return
        except Exception:
            await session.rollback()
            logging.exception("Manual order grant failed")
            await callback.answer("Не получилось выдать ключ.", show_alert=True)
            return

    notified = await notify_paid_order(bot, result.order_id, force=True)
    await callback.message.answer(
        admin_manual_grant_result_text(result, notified),
        parse_mode=ParseMode.HTML,
        reply_markup=kb.admin_keyboard(),
    )
    await callback.answer("Готово")


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
    chat_id = normalize_public_key_chat_id(settings.public_key_chat_id)
    if not chat_id:
        await message.answer("PUBLIC_KEY_CHAT_ID не задан в .env.", reply_markup=kb.admin_keyboard())
        return
    async with SessionLocal() as session:
        key = await get_active_public_key(session) or await rotate_public_key(session)
        text = await public_key_channel_post_text(session, key)
        await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
        await mark_public_key_posted(session)
    await message.answer(f"Бесплатный ключ опубликован в {chat_id}.", reply_markup=kb.admin_keyboard())


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
