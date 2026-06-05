from html import escape
from decimal import Decimal

from app.config import get_settings
from app.models import Order, Plan, Subscription, User, VpnKey
from app.services.public_keys import public_key_post_text


def start_text(user: User, is_admin: bool) -> str:
    role = "админ" if is_admin else "пользователь"
    return (
        f"MiloshVPN запущен. Ты вошел как {role}.\n\n"
        "Выбирай тариф, оплачивай через DonationAlerts с персональным кодом, получай VLESS-ключ."
    )


def profile_text(user: User) -> str:
    username = f"@{escape(user.username)}" if user.username else "не указан"
    return (
        "Профиль\n\n"
        f"Telegram ID: <code>{user.telegram_id}</code>\n"
        f"Username: {username}\n"
        f"Роль: {user.role}"
    )


def plans_text(plans: list[Plan]) -> str:
    lines = ["Пакеты MiloshVPN\n"]
    for plan in plans:
        traffic = "без лимита" if plan.traffic_gb is None else f"{plan.traffic_gb} ГБ"
        lines.append(f"{plan.title}: {format_price(plan.price_rub)} RUB, {plan.days} дней, {traffic}")
        lines.append(plan.description)
        lines.append("")
    return "\n".join(lines).strip()


def payment_text(order: Order, plan: Plan) -> str:
    settings = get_settings()
    donate_url = escape(settings.donationalerts_donate_url or "DonationAlerts URL еще не задан в .env")
    return (
        "Заказ создан.\n\n"
        f"Тариф: {escape(plan.title)}\n"
        f"Сумма: {format_price(order.amount_rub)} RUB\n"
        f"Код оплаты: <code>{order.payment_code}</code>\n\n"
        "Открой DonationAlerts и вставь этот код в сообщение к донату. "
        "По нему backend поймет, что оплатил именно твой Telegram.\n\n"
        f"{donate_url}\n\n"
        "После оплаты нажми <b>Проверить оплату</b>. Если DonationAlerts token настроен, проверка пройдет без вебхуков."
    )


def subscription_text(subscription: Subscription | None, key: VpnKey | None) -> str:
    if subscription is None:
        return "Активной подписки пока нет. Нажми <b>Купить пакет</b>."

    traffic = "без лимита" if subscription.traffic_limit_gb is None else f"{subscription.traffic_limit_gb} ГБ"
    text = (
        "Моя подписка\n\n"
        f"Тариф: {subscription.plan_code}\n"
        f"Действует до: {subscription.expires_at:%d.%m.%Y %H:%M UTC}\n"
        f"Трафик: {traffic}"
    )
    if key is not None:
        text += f"\n\nVLESS ключ:\n<code>{escape(key.vless_uri)}</code>"
    else:
        text += "\n\nКлюч еще не выдан. Напиши админу или попробуй продлить подписку."
    return text


def free_key_text(key: VpnKey | None) -> str:
    if key is None:
        return "Бесплатного ключа пока нет. Админ может создать его в админке."
    return public_key_post_text(key)


def instruction_text() -> str:
    return (
        "Инструкция\n\n"
        "1. Установи V2RayNG, Hiddify, Streisand или другой клиент с VLESS.\n"
        "2. Скопируй VLESS-ключ из <b>Моя подписка</b>.\n"
        "3. Импортируй ключ из буфера обмена.\n"
        "4. Подключись и проверь сайты.\n\n"
        "Оплата работает так: бот создает персональный код, ты вставляешь его в сообщение DonationAlerts, "
        "worker на backend проверяет донаты polling-ом и активирует подписку."
    )


def admin_help_text() -> str:
    return (
        "Админка\n\n"
        "<code>/add_admin TELEGRAM_ID</code> - добавить админа.\n"
        "<code>/rotate_free</code> - пересоздать бесплатный ключ.\n"
        "<code>/post_free</code> - опубликовать бесплатный ключ в канал/чат из PUBLIC_KEY_CHAT_ID."
    )


def format_price(value: Decimal) -> str:
    return str(value.quantize(Decimal("1"))) if value == value.quantize(Decimal("1")) else str(value)
