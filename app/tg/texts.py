from html import escape
from decimal import Decimal

from app.config import get_settings
from app.models import Order, Plan, Subscription, User, VpnKey
from app.services.vpn import TRIAL_PLAN_CODE


def start_text(user: User, is_admin: bool, trial_created: bool = False, trial_failed: bool = False) -> str:
    if trial_failed:
        trial_line = "Trial-ключ не выдался автоматически. Загляни в поддержку, поможем без паники."
    elif trial_created:
        trial_line = "Подарок на входе: 7-дневный ключ уже в твоём Профиле."
    else:
        trial_line = "Ключ, подписка и сроки лежат в Профиле."
    admin_line = "\n\n🛠 Админка тоже рядом, отдельной кнопкой." if is_admin else ""
    return (
        f"✨ Добро пожаловать, дорогой друг!\n\n"
        "MiloshVPN — сервис, который даст тебе доступ ко всем сервисам и белым спискам по доступным ценам.\n\n"
        f"🎁 {trial_line}\n"
        "🛒 Хочешь полный доступ — жми Купить, выбирай тариф и после оплаты нажимай Проверить оплату."
        f"{admin_line}"
    )


def profile_text(user: User, subscription: Subscription | None, key: VpnKey | None, plan: Plan | None = None) -> str:
    username = f"@{escape(user.username)}" if user.username else "не указан"
    lines = [
        "👤 Профиль\n\n"
        f"Ник: {username}"
    ]

    if subscription is None:
        lines.append("\n\nПодписка: пока нет активной")
        lines.append("\nTrial выдаётся один раз при старте бота. Если он не появился, напиши в поддержку.")
        return "".join(lines)

    title = plan.title if plan is not None else subscription.plan_code
    if subscription.plan_code == TRIAL_PLAN_CODE:
        title = "Бесплатный 7-дневный ключ"
    traffic = "без лимита" if subscription.traffic_limit_gb is None else f"{subscription.traffic_limit_gb} ГБ"
    lines.append(
        "\n\n💎 Моя подписка\n"
        f"Тариф: <b>{escape(title)}</b>\n"
        f"Старт: {subscription.starts_at:%d.%m.%Y %H:%M UTC}\n"
        f"Финиш: {subscription.expires_at:%d.%m.%Y %H:%M UTC}\n"
        f"Лимит: {traffic}"
    )

    if key is not None:
        lines.append(f"\n\n🔑 Мой ключ:\n<code>{escape(key.vless_uri)}</code>")
    else:
        lines.append("\n\n🔑 Ключ пока не выдан. Открой /start или напиши в поддержку.")
    return "".join(lines)


def plans_text(plans: list[Plan]) -> str:
    lines = ["🛒 Тарифы MiloshVPN\n"]
    for plan in plans:
        traffic = "без лимита" if plan.traffic_gb is None else f"{plan.traffic_gb} ГБ"
        lines.append(f"💠 <b>{escape(plan.title)}</b>")
        lines.append(f"Цена: <b>{format_price(plan.price_rub)} RUB</b>")
        lines.append(f"Срок: {plan.days} дней")
        lines.append(f"Трафик: {traffic}")
        lines.append(escape(plan.description))
        lines.append("")
    return "\n".join(lines).strip()


def payment_text(order: Order, plan: Plan) -> str:
    required_amount = max(Decimal(order.amount_rub), Decimal(plan.price_rub))
    code_frame = f"<pre>----\n{escape(order.payment_code)}\n----</pre>"
    return (
        "🧾 Заказ готов\n\n"
        f"Тариф: <b>{escape(plan.title)}</b>\n"
        f"Сумма: <b>{format_price(required_amount)} RUB</b>\n\n"
        "👇 Код для сообщения DonationAlerts. Нажми по нему и скопируй:\n"
        f"{code_frame}\n"
        "Как оплатить:\n"
        "1. Нажми кнопку <b>Оплатить</b> ниже.\n"
        "2. В DonationAlerts вручную поставь сумму из этого сообщения.\n"
        "3. В поле сообщения вставь код из рамки.\n"
        "4. Если DonationAlerts открыл EUR или 10 ₽, выбери RUB и впиши сумму тарифа руками.\n"
        "5. После оплаты вернись в бот и нажми <b>Проверить оплату</b>.\n\n"
        "Важно: backend засчитает только правильную сумму в RUB/RUR и именно этот код."
    )


def subscription_text(subscription: Subscription | None, key: VpnKey | None) -> str:
    if subscription is None:
        return "Подписки пока нет. Жми <b>Купить</b>, выберем тебе хороший вход."

    traffic = "без лимита" if subscription.traffic_limit_gb is None else f"{subscription.traffic_limit_gb} ГБ"
    title = "Бесплатный 7-дневный ключ" if subscription.plan_code == TRIAL_PLAN_CODE else subscription.plan_code
    text = (
        "💎 Моя подписка\n\n"
        f"Тариф: {title}\n"
        f"Старт: {subscription.starts_at:%d.%m.%Y %H:%M UTC}\n"
        f"Финиш: {subscription.expires_at:%d.%m.%Y %H:%M UTC}\n"
        f"Лимит: {traffic}"
    )
    if key is not None:
        text += f"\n\n🔑 VLESS ключ:\n<code>{escape(key.vless_uri)}</code>"
    else:
        text += "\n\nКлюч ещё не выдан. Напиши в поддержку, разберёмся."
    return text


def admin_key_text(key: VpnKey) -> str:
    return (
        "Админский ключ создан без оплаты.\n\n"
        f"Label: <code>{escape(key.email)}</code>\n"
        f"VLESS ключ:\n<code>{escape(key.vless_uri)}</code>"
    )


def instruction_text() -> str:
    return (
        "📘 Инструкция\n\n"
        "1. Установи V2RayNG, Hiddify, Streisand или другой клиент с VLESS.\n"
        "2. Открой <b>Профиль</b> и скопируй VLESS-ключ.\n"
        "3. Импортируй ключ из буфера обмена.\n"
        "4. Подключись и проверь Telegram, YouTube, Instagram и обычные сайты.\n\n"
        "По оплате всё просто: бот даёт персональный код, ты вставляешь его в сообщение DonationAlerts, "
        "а система сама найдёт оплату и продлит доступ."
    )


def policy_text() -> str:
    return (
        "🛡 Политика проекта\n\n"
        "1. Один ключ — один владелец и один нормальный сценарий использования.\n"
        "2. Trial даётся один раз на 7 дней и имеет лимит трафика.\n"
        "3. Торренты, Tor, спам, сканирование и жёсткая нагрузка запрещены.\n"
        "4. Оплата засчитывается только с правильной суммой и персональным кодом.\n"
        "5. Если ключ начинает вредить серверу, его могут отключить."
    )


def support_text() -> str:
    return (
        "💬 Поддержка\n\n"
        "Что-то не подключилось, оплата потерялась или ключ чудит? Пиши сюда: @vandavolmink"
    )


def admin_help_text() -> str:
    settings = get_settings()
    panel_url = settings.admin_panel_url
    if settings.admin_web_token:
        separator = "&" if "?" in panel_url else "?"
        panel_url = f"{panel_url}{separator}token={settings.admin_web_token}"

    return (
        "🛠 Админка MiloshVPN\n\n"
        f'Web-панель: <a href="{escape(panel_url)}">открыть</a>\n\n'
        "Что можно сделать здесь:\n"
        "• посмотреть статистику и ожидающие оплаты;\n"
        "• увидеть личные ключи и удалить лишний;\n"
        "• создать admin key без оплаты;\n"
        "• управлять бесплатным публичным ключом.\n\n"
        "<code>/add_admin TELEGRAM_ID</code> — добавить админа.\n"
        "<code>/admin_key</code> — создать admin key."
    )


def format_price(value: Decimal) -> str:
    return str(value.quantize(Decimal("1"))) if value == value.quantize(Decimal("1")) else str(value)
