from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


PROFILE = "Профиль"
SUBSCRIPTION = "Моя подписка"
BUY = "Купить"
EXTEND = "Продлить"
FREE_KEY = "Бесплатный ключ"
HELP = "Инструкция"
POLICY = "Политика проекта"
SUPPORT = "Поддержка"
ADMIN = "Админка"

ADMIN_STATS = "Статистика"
ADMIN_ROTATE_FREE = "Пересоздать free key"
ADMIN_POST_FREE = "Опубликовать free key"
ADMIN_PENDING = "Ожидающие оплаты"
ADMIN_REVIEW_ORDERS = "Проверить заказы"
ADMIN_FIND_ORDER = "Найти оплату"
ADMIN_PAYMENT_MODE = "Система оплаты"
ADMIN_CREATE_KEY = "Создать admin key"
ADMIN_KEYS = "Личные ключи"
ADMIN_MAIN_MENU = "Главное меню"


def main_keyboard(is_admin: bool) -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton(text=BUY), KeyboardButton(text=PROFILE)],
        [KeyboardButton(text=HELP), KeyboardButton(text=POLICY)],
        [KeyboardButton(text=SUPPORT)],
    ]
    if is_admin:
        keyboard.append([KeyboardButton(text=ADMIN)])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def admin_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=ADMIN_STATS), KeyboardButton(text=ADMIN_PENDING)],
            [KeyboardButton(text=ADMIN_REVIEW_ORDERS), KeyboardButton(text=ADMIN_FIND_ORDER)],
            [KeyboardButton(text=ADMIN_KEYS), KeyboardButton(text=ADMIN_PAYMENT_MODE)],
            [KeyboardButton(text=ADMIN_CREATE_KEY), KeyboardButton(text=ADMIN_ROTATE_FREE)],
            [KeyboardButton(text=ADMIN_POST_FREE)],
            [KeyboardButton(text=PROFILE), KeyboardButton(text=ADMIN_MAIN_MENU)],
        ],
        resize_keyboard=True,
    )


def plans_keyboard(include_admin_test: bool = False) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton(text="💠 75 RUB — Базовый тариф", callback_data="buy:basic")],
        [InlineKeyboardButton(text="🚀 95 RUB — Без ограничений", callback_data="buy:unlimited")],
    ]
    if include_admin_test:
        keyboard.append([InlineKeyboardButton(text="🧪 10 RUB — Тестовый тариф", callback_data="buy:admin_test")])
    return InlineKeyboardMarkup(
        inline_keyboard=keyboard
    )


def check_payment_keyboard(order_id: int, payment_url: str | None = None) -> InlineKeyboardMarkup:
    buttons = []
    if payment_url:
        buttons.append([InlineKeyboardButton(text="Оплатить", url=payment_url)])
    buttons.extend(
        [
            [InlineKeyboardButton(text="Проверить оплату", callback_data=f"check_payment:{order_id}")],
            [InlineKeyboardButton(text="Купить другой тариф", callback_data="show_plans")],
        ]
    )
    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


def manual_sbp_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="СБП", callback_data=f"manual_sbp:{order_id}")],
            [InlineKeyboardButton(text="Проверить оплату", callback_data=f"check_payment:{order_id}")],
            [InlineKeyboardButton(text="Купить другой тариф", callback_data="show_plans")],
        ]
    )


def payment_provider_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="DonationAlerts", callback_data="admin_paymode_select:donationalerts")],
            [InlineKeyboardButton(text="Ручная SBP", callback_data="admin_paymode_select:manual_sbp")],
            [InlineKeyboardButton(text="Гибрид", callback_data="admin_paymode_select:hybrid")],
        ]
    )


def confirm_payment_provider_keyboard(provider: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да", callback_data=f"admin_paymode_confirm:{provider}"),
                InlineKeyboardButton(text="Нет", callback_data="admin_paymode_cancel"),
            ]
        ]
    )


def profile_actions_keyboard(*, admin_url: str | None = None, include_replace: bool = False) -> InlineKeyboardMarkup:
    keyboard: list[list[InlineKeyboardButton]] = []
    if include_replace:
        keyboard.append([InlineKeyboardButton(text="Заменить sub", callback_data="replace_key")])
    if admin_url:
        keyboard.append([InlineKeyboardButton(text="Открыть профиль", url=admin_url)])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def admin_profile_keyboard(url: str) -> InlineKeyboardMarkup:
    return profile_actions_keyboard(admin_url=url)
