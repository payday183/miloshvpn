from html import escape
from decimal import Decimal

from app.config import get_settings
from app.models import Order, Plan, Subscription, User, VpnKey
from app.services.payment_links import payment_page_url
from app.services.vpn import TRIAL_PLAN_CODE


def start_text(user: User, is_admin: bool, trial_created: bool = False, trial_failed: bool = False) -> str:
    greeting = f"Привет, {escape(user.first_name)}!" if user.first_name else "Привет!"
    if trial_failed:
        trial_line = "Не смог автоматически выдать trial-ключ. Напиши в поддержку, если ключ не появился в Профиле."
    elif trial_created:
        trial_line = "Бесплатный 7-дневный ключ уже выдан автоматически. Открой Профиль, чтобы скопировать его."
    else:
        trial_line = "Твой ключ, подписка и сроки находятся в Профиле."
    admin_line = "\n\nАдминка доступна отдельной кнопкой." if is_admin else ""
    return (
        f"{greeting}\n\n"
        "Это MiloshVPN: быстрый доступ через VLESS-ключ, тарифы и trial в одном боте.\n\n"
        f"{trial_line}\n"
        "Для покупки нажми Купить, после оплаты нажми Проверить оплату."
        f"{admin_line}"
    )


def profile_text(user: User, subscription: Subscription | None, key: VpnKey | None, plan: Plan | None = None) -> str:
    username = f"@{escape(user.username)}" if user.username else "не указан"
    lines = [
        "Профиль\n\n"
        f"Telegram ID: <code>{user.telegram_id}</code>\n"
        f"Username: {username}\n"
        f"Роль: {user.role}"
    ]

    if subscription is None:
        lines.append("\n\nПодписка: нет активной подписки")
        lines.append("\nБесплатный trial выдаётся один раз при старте бота.")
        return "".join(lines)

    title = plan.title if plan is not None else subscription.plan_code
    if subscription.plan_code == TRIAL_PLAN_CODE:
        title = "Бесплатный 7-дневный ключ"
    traffic = "без лимита" if subscription.traffic_limit_gb is None else f"{subscription.traffic_limit_gb} ГБ"
    lines.append(
        "\n\nМоя подписка\n"
        f"Тариф: {escape(title)}\n"
        f"Начало: {subscription.starts_at:%d.%m.%Y %H:%M UTC}\n"
        f"Окончание: {subscription.expires_at:%d.%m.%Y %H:%M UTC}\n"
        f"Трафик: {traffic}"
    )

    if key is not None:
        lines.append(f"\n\nМой ключ:\n<code>{escape(key.vless_uri)}</code>")
    else:
        lines.append("\n\nМой ключ: пока не выдан, попробуй открыть /start или напиши в поддержку.")
    return "".join(lines)


def plans_text(plans: list[Plan]) -> str:
    lines = ["Пакеты MiloshVPN\n"]
    for plan in plans:
        traffic = "без лимита" if plan.traffic_gb is None else f"{plan.traffic_gb} ГБ"
        lines.append(f"{plan.title}: {format_price(plan.price_rub)} RUB, {plan.days} дней, {traffic}")
        lines.append(plan.description)
        lines.append("")
    return "\n".join(lines).strip()


def payment_text(order: Order, plan: Plan) -> str:
    required_amount = max(Decimal(order.amount_rub), Decimal(plan.price_rub))
    return (
        "Заказ создан.\n\n"
        f"Тариф: {escape(plan.title)}\n"
        f"Проверьте, чтобы сумма была <b>{format_price(required_amount)} RUB</b>.\n"
        f"Сообщение к донату: <code>{escape(order.payment_code)}</code>\n"
        f'<a href="{escape(payment_page_url(order))}">Скопировать код и оплатить</a>\n\n'
        "Нажми кнопку оплаты ниже: код скопируется кликом, потом откроется DonationAlerts.\n"
        "После оплаты нажми <b>Проверить оплату</b>."
    )


def subscription_text(subscription: Subscription | None, key: VpnKey | None) -> str:
    if subscription is None:
        return "Активной подписки пока нет. Нажми <b>Купить</b>."

    traffic = "без лимита" if subscription.traffic_limit_gb is None else f"{subscription.traffic_limit_gb} ГБ"
    title = "Бесплатный 7-дневный ключ" if subscription.plan_code == TRIAL_PLAN_CODE else subscription.plan_code
    text = (
        "Моя подписка\n\n"
        f"Тариф: {title}\n"
        f"Начало: {subscription.starts_at:%d.%m.%Y %H:%M UTC}\n"
        f"Окончание: {subscription.expires_at:%d.%m.%Y %H:%M UTC}\n"
        f"Трафик: {traffic}"
    )
    if key is not None:
        text += f"\n\nVLESS ключ:\n<code>{escape(key.vless_uri)}</code>"
    else:
        text += "\n\nКлюч еще не выдан. Напиши админу или попробуй продлить подписку."
    return text


def admin_key_text(key: VpnKey) -> str:
    return (
        "Админский ключ создан без оплаты.\n\n"
        f"Label: <code>{escape(key.email)}</code>\n"
        f"VLESS ключ:\n<code>{escape(key.vless_uri)}</code>"
    )


def instruction_text() -> str:
    return (
        "Инструкция\n\n"
        "1. Установи V2RayNG, Hiddify, Streisand или другой клиент с VLESS.\n"
        "2. Скопируй VLESS-ключ из <b>Профиль</b>.\n"
        "3. Импортируй ключ из буфера обмена.\n"
        "4. Подключись и проверь сайты.\n\n"
        "Оплата работает так: бот создает персональный код, ты вставляешь его в сообщение DonationAlerts, "
        "worker на backend проверяет донаты polling-ом и активирует подписку."
    )


def policy_text() -> str:
    return (
        "Политика проекта\n\n"
        "1. Один ключ привязан к одному Telegram ID и рассчитан на личное использование.\n"
        "2. Trial-ключ выдается один раз на 7 дней и имеет лимит трафика.\n"
        "3. Торренты, Tor, спам, сканирование и перегруз сервера запрещены.\n"
        "4. Оплата засчитывается только в RUB/RUR, с правильной суммой тарифа и персональным кодом.\n"
        "5. При нарушении правил ключ может быть отключен."
    )


def support_text() -> str:
    return (
        "Поддержка\n\n"
        "Если возникли проблемы с оплатой, ключом или подключением, напиши: @vandavolmink"
    )


def admin_help_text() -> str:
    settings = get_settings()
    panel_url = settings.admin_panel_url
    if settings.admin_web_token:
        separator = "&" if "?" in panel_url else "?"
        panel_url = f"{panel_url}{separator}token={settings.admin_web_token}"

    return (
        "Админка\n\n"
        f'Web-панель: <a href="{escape(panel_url)}">открыть</a>\n\n'
        "<code>/add_admin TELEGRAM_ID</code> - добавить админа.\n"
        "<code>/admin_key</code> - создать админский ключ без оплаты.\n"
        "<code>/rotate_free</code> - пересоздать бесплатный ключ.\n"
        "<code>/post_free</code> - опубликовать бесплатный ключ в канал/чат из PUBLIC_KEY_CHAT_ID."
    )


def format_price(value: Decimal) -> str:
    return str(value.quantize(Decimal("1"))) if value == value.quantize(Decimal("1")) else str(value)
