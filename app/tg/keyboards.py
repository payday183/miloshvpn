from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


PROFILE_LEGACY = "Профиль"
SUBSCRIPTION_LEGACY = "Моя подписка"
BUY_LEGACY = "Купить"
EXTEND_LEGACY = "Продлить"
FREE_KEY_LEGACY = "Бесплатный ключ"
HELP_LEGACY = "Инструкция"
POLICY_LEGACY = "Политика проекта"
SUPPORT_LEGACY = "Поддержка"
ADMIN_LEGACY = "Админка"
REFERRALS_LEGACY = "Пригласить друга"

PROFILE = "👤 Профиль"
SUBSCRIPTION = "💎 Моя подписка"
BUY = "🛒 Купить"
EXTEND = "🔁 Продлить"
FREE_KEY = "🎁 Бесплатный ключ"
HELP = "📘 Инструкция"
POLICY = "🛡 Политика проекта"
SUPPORT = "💬 Поддержка"
ADMIN = "🛠 Админка"
REFERRALS = "🤝 Пригласить друга"

PROFILE_TEXTS = (PROFILE, PROFILE_LEGACY)
SUBSCRIPTION_TEXTS = (SUBSCRIPTION, SUBSCRIPTION_LEGACY)
BUY_TEXTS = (BUY, BUY_LEGACY, "Купить пакет")
EXTEND_TEXTS = (EXTEND, EXTEND_LEGACY)
FREE_KEY_TEXTS = (FREE_KEY, FREE_KEY_LEGACY)
HELP_TEXTS = (HELP, HELP_LEGACY)
POLICY_TEXTS = (POLICY, POLICY_LEGACY)
SUPPORT_TEXTS = (SUPPORT, SUPPORT_LEGACY)
ADMIN_TEXTS = (ADMIN, ADMIN_LEGACY)
REFERRALS_TEXTS = (REFERRALS, REFERRALS_LEGACY)

ADMIN_STATS_LEGACY = "Статистика"
ADMIN_ROTATE_FREE_LEGACY = "Пересоздать free key"
ADMIN_POST_FREE_LEGACY = "Опубликовать free key"
ADMIN_PENDING_LEGACY = "Ожидающие оплаты"
ADMIN_REVIEW_ORDERS_LEGACY = "Проверить заказы"
ADMIN_FIND_ORDER_LEGACY = "Найти оплату"
ADMIN_PAYMENT_MODE_LEGACY = "Система оплаты"
ADMIN_CREATE_KEY_LEGACY = "Создать admin key"
ADMIN_DIRECT_NODE_AUDIT_LEGACY = "Direct-node audit"
ADMIN_DIRECT_NODE_CREATE_LEGACY = "Direct-node create"
ADMIN_KEYS_LEGACY = "Личные ключи"
ADMIN_MAIN_MENU_LEGACY = "Главное меню"

ADMIN_STATS = "📊 Статистика"
ADMIN_ROTATE_FREE = "🔄 Пересоздать free key"
ADMIN_POST_FREE = "📣 Опубликовать free key"
ADMIN_PENDING = "⏳ Ожидающие оплаты"
ADMIN_REVIEW_ORDERS = "🔎 Проверить заказы"
ADMIN_FIND_ORDER = "🧾 Найти оплату"
ADMIN_PAYMENT_MODE = "💳 Система оплаты"
ADMIN_CREATE_KEY = "🔐 Создать admin key"
ADMIN_DIRECT_NODE_AUDIT = "🧪 Direct-node audit"
ADMIN_DIRECT_NODE_CREATE = "⚙️ Direct-node create"
ADMIN_KEYS = "🔑 Личные ключи"
ADMIN_MAIN_MENU = "🏠 Главное меню"

ADMIN_STATS_TEXTS = (ADMIN_STATS, ADMIN_STATS_LEGACY)
ADMIN_ROTATE_FREE_TEXTS = (ADMIN_ROTATE_FREE, ADMIN_ROTATE_FREE_LEGACY)
ADMIN_POST_FREE_TEXTS = (ADMIN_POST_FREE, ADMIN_POST_FREE_LEGACY)
ADMIN_PENDING_TEXTS = (ADMIN_PENDING, ADMIN_PENDING_LEGACY)
ADMIN_REVIEW_ORDERS_TEXTS = (ADMIN_REVIEW_ORDERS, ADMIN_REVIEW_ORDERS_LEGACY)
ADMIN_FIND_ORDER_TEXTS = (ADMIN_FIND_ORDER, ADMIN_FIND_ORDER_LEGACY)
ADMIN_PAYMENT_MODE_TEXTS = (ADMIN_PAYMENT_MODE, ADMIN_PAYMENT_MODE_LEGACY)
ADMIN_CREATE_KEY_TEXTS = (ADMIN_CREATE_KEY, ADMIN_CREATE_KEY_LEGACY)
ADMIN_DIRECT_NODE_AUDIT_TEXTS = (ADMIN_DIRECT_NODE_AUDIT, ADMIN_DIRECT_NODE_AUDIT_LEGACY)
ADMIN_DIRECT_NODE_CREATE_TEXTS = (ADMIN_DIRECT_NODE_CREATE, ADMIN_DIRECT_NODE_CREATE_LEGACY)
ADMIN_KEYS_TEXTS = (ADMIN_KEYS, ADMIN_KEYS_LEGACY)
ADMIN_MAIN_MENU_TEXTS = (ADMIN_MAIN_MENU, ADMIN_MAIN_MENU_LEGACY)

CHANNEL_GATE_SUBSCRIBED = "channel_gate_subscribed"
REFERRAL_COPY = "referral_copy"
TRIAL_SHOW_REFERRALS = "trial_show_referrals"
TRIAL_FEEDBACK_PREFIX = "trial_feedback:"


def main_keyboard(is_admin: bool) -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton(text=BUY), KeyboardButton(text=PROFILE)],
        [KeyboardButton(text=HELP), KeyboardButton(text=POLICY)],
        [KeyboardButton(text=SUPPORT), KeyboardButton(text=REFERRALS)],
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
            [KeyboardButton(text=ADMIN_DIRECT_NODE_AUDIT), KeyboardButton(text=ADMIN_DIRECT_NODE_CREATE)],
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


def channel_gate_keyboard(channel_url: str) -> InlineKeyboardMarkup:
    keyboard: list[list[InlineKeyboardButton]] = []
    if channel_url:
        keyboard.append([InlineKeyboardButton(text="Канал", url=channel_url)])
    keyboard.append([InlineKeyboardButton(text="Я подписался", callback_data=CHANNEL_GATE_SUBSCRIBED)])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def referral_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📋 Скопировать ссылку", callback_data=REFERRAL_COPY)],
        ]
    )


def trial_expiry_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💠 Купить базовый тариф 75 RUB", callback_data="buy:basic")],
            [InlineKeyboardButton(text="🤝 Пригласить друга", callback_data=TRIAL_SHOW_REFERRALS)],
        ]
    )


def trial_feedback_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="отлично", callback_data=f"{TRIAL_FEEDBACK_PREFIX}excellent"),
                InlineKeyboardButton(text="хорошо", callback_data=f"{TRIAL_FEEDBACK_PREFIX}good"),
            ],
            [
                InlineKeyboardButton(text="удовлетворительно", callback_data=f"{TRIAL_FEEDBACK_PREFIX}satisfactory"),
            ],
            [
                InlineKeyboardButton(text="сам напешууууУ", callback_data=f"{TRIAL_FEEDBACK_PREFIX}custom"),
            ],
        ]
    )
