from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


PROFILE = "Профиль"
SUBSCRIPTION = "Моя подписка"
BUY = "Купить пакет"
EXTEND = "Продлить"
FREE_KEY = "Бесплатный ключ"
HELP = "Инструкция"
ADMIN = "Админка"

ADMIN_STATS = "Статистика"
ADMIN_ROTATE_FREE = "Пересоздать free key"
ADMIN_POST_FREE = "Опубликовать free key"
ADMIN_PENDING = "Ожидающие оплаты"
ADMIN_CREATE_KEY = "Создать admin key"


def main_keyboard(is_admin: bool) -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton(text=PROFILE), KeyboardButton(text=SUBSCRIPTION)],
        [KeyboardButton(text=BUY), KeyboardButton(text=EXTEND)],
        [KeyboardButton(text=FREE_KEY), KeyboardButton(text=HELP)],
    ]
    if is_admin:
        keyboard.append([KeyboardButton(text=ADMIN)])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def admin_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=ADMIN_STATS), KeyboardButton(text=ADMIN_PENDING)],
            [KeyboardButton(text=ADMIN_ROTATE_FREE), KeyboardButton(text=ADMIN_POST_FREE)],
            [KeyboardButton(text=ADMIN_CREATE_KEY), KeyboardButton(text=PROFILE)],
        ],
        resize_keyboard=True,
    )


def plans_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="75 RUB - Дешевый тариф", callback_data="buy:basic")],
            [InlineKeyboardButton(text="95 RUB - Без ограничений", callback_data="buy:unlimited")],
        ]
    )


def check_payment_keyboard(order_id: int, payment_url: str | None = None) -> InlineKeyboardMarkup:
    buttons = []
    if payment_url:
        buttons.append([InlineKeyboardButton(text="Скопировать код и оплатить", url=payment_url)])
    buttons.extend(
        [
            [InlineKeyboardButton(text="Проверить оплату", callback_data=f"check_payment:{order_id}")],
            [InlineKeyboardButton(text="Купить другой тариф", callback_data="show_plans")],
        ]
    )
    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


def admin_profile_keyboard(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Открыть профиль", url=url)],
        ]
    )
