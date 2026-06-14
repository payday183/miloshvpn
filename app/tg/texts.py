from html import escape
from math import ceil
from decimal import Decimal

from app.config import get_settings
from app.models import Order, Plan, Subscription, User, VpnKey
from app.services.manual_orders import ManualOrderGrantResult
from app.services.payment_modes import payment_provider_label
from app.services.vpn import TRIAL_PLAN_CODE
from app.timeutils import utcnow


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
        f"🎁 {trial_line}\n\n"
        "🛒 Хочешь полный доступ — жми Купить, выбирай тариф и после оплаты нажимай Проверить оплату."
        f"{admin_line}"
    )


def profile_text(
    user: User,
    subscription: Subscription | None,
    key: VpnKey | None,
    plan: Plan | None = None,
    *,
    referral_url: str = "",
    referral_count: int = 0,
) -> str:
    username = f"@{escape(user.username)}" if user.username else "не указан"
    lines = [
        "👤 Профиль\n\n"
        f"Ник: {username}"
    ]

    if subscription is None:
        lines.append("\n\nПодписка: пока нет активной")
        lines.append("\nTrial выдаётся один раз при старте бота. Если он не появился, напиши в поддержку.")
        return "".join(lines)

    title = subscription_title(subscription, plan)
    traffic = "без лимита" if subscription.traffic_limit_gb is None else f"{subscription.traffic_limit_gb} ГБ"
    lines.append(
        f"\n\n💎 Моя подписка: <b>{escape(title)}</b>\n"
    )
    if referral_url:
        safe_referral_url = escape(referral_url)
        lines.append(
            "\nРеферальная ссылка: посоветуй другу — получи +3 дня\n"
            f"<code>{safe_referral_url}</code>\n"
            "\n"
            f"Число рефералов: <b>{referral_count}</b>\n"
        )

    lines.append(
        f"\nСтарт: {subscription.starts_at:%d.%m.%Y %H:%M UTC}\n"
        f"Финиш: {subscription.expires_at:%d.%m.%Y %H:%M UTC}\n"
        "\n"
        f"Лимит: {traffic}"
    )

    if key is not None:
        server = escape(key.server_label or "системная 3x-ui")
        lines.append(f"\n\nОсталось: {remaining_days_text(subscription)}")
        lines.append(f"\n\n🌍 Сервер: {server}")
        lines.append(f"\n\n🔑 Мой sub:\n<code>{escape(key.vless_uri)}</code>")
    else:
        lines.append("\n\n🔑 Ключ пока не выдан. Открой /start или напиши в поддержку.")
    return "".join(lines)


def channel_gate_text() -> str:
    return (
        "Перед профилем подпишись на канал MiloshVPN.\n\n"
        "В канале публикуем новости по серверам, обновления тарифов, полезные подсказки по подключению "
        "и бесплатные ключи для подписчиков.\n\n"
        "После подписки нажми <b>Я подписался</b> — покажу твой профиль и подарок: "
        "VPN-ключ на 7 дней с лимитом 500 ГБ бесплатно."
    )


def subscription_title(subscription: Subscription, plan: Plan | None = None) -> str:
    if subscription.plan_code == TRIAL_PLAN_CODE:
        return "Триал"
    return plan.title if plan is not None else subscription.plan_code


def remaining_days_text(subscription: Subscription) -> str:
    seconds_left = max(0, (subscription.expires_at - utcnow()).total_seconds())
    days_left = ceil(seconds_left / 86400) if seconds_left > 0 else 0
    return f"{days_left} {days_word(days_left)}"


def days_word(value: int) -> str:
    if value % 10 == 1 and value % 100 != 11:
        return "день"
    if value % 10 in {2, 3, 4} and value % 100 not in {12, 13, 14}:
        return "дня"
    return "дней"


def plans_text(plans: list[Plan]) -> str:
    lines = ["🛒 Тарифы MiloshVPN", ""]
    for plan in plans:
        traffic = "без лимита" if plan.traffic_gb is None else f"{plan.traffic_gb} ГБ"
        lines.append(f"💠 {escape(plan.title)}")
        lines.append("")
        lines.append(f"Цена: {format_price(plan.price_rub)} RUB")
        lines.append("")
        lines.append(f"Срок: {plan.days} дней")
        lines.append("")
        lines.append(f"Трафик: {traffic}")
        lines.append("")
        lines.append(escape(plan.description))
        lines.append("")
    return "\n".join(lines).strip()


def payment_text(order: Order, plan: Plan) -> str:
    required_amount = max(Decimal(order.amount_rub), Decimal(plan.price_rub))
    code_frame = f"----\n<code>{escape(order.payment_code)}</code>\n----"
    return (
        "🧾 Заказ готов\n\n"
        f"Тариф: <b>{escape(plan.title)}</b>\n"
        f"\nСумма: <b>{format_price(required_amount)} RUB</b>\n\n"
        "👇 Код для сообщения DonationAlerts. Нажми по нему и скопируй:\n"
        f"{code_frame}\n"
        "Как оплатить:\n"
        "\n1. Нажми кнопку <b>Оплатить</b> ниже.\n"
        "\n2. В DonationAlerts вручную поставь сумму из этого сообщения.\n"
        "\n3. В поле сообщения вставь код из рамки.\n"
        "\n4. Если DonationAlerts открыл EUR или 10 ₽, выбери RUB и впиши сумму тарифа руками.\n"
        "\n"
        "5. После оплаты вернись в бот и нажми <b>Проверить оплату</b>.\n\n"
        "Важно: backend засчитает только правильную сумму в RUB/RUR и именно этот код.\n\n"
        "MiloshVPN работает на поддержку проекта: суммы помогают оплачивать серверы и держать сервис живым."
    )


def hybrid_payment_text(order: Order, plan: Plan) -> str:
    return (
        f"{payment_text(order, plan)}\n\n"
        "⚡ Сейчас включён гибридный режим: после кнопки <b>Проверить оплату</b> "
        "бот сразу выдаст временный доступ, а админ спокойно сверит оплату."
    )


def manual_sbp_payment_text(order: Order, plan: Plan) -> str:
    return (
        "🧾 Заказ готов\n\n"
        f"Тариф: <b>{escape(plan.title)}</b>\n"
        f"\nСумма: <b>{format_price(Decimal(order.amount_rub))} RUB</b>\n\n"
        "Выберите способ оплаты ниже.\n\n"
        "После оплаты нажмите <b>Проверить оплату</b>. Бот выдаст временный доступ, "
        "а админ проверит платёж и закрепит тариф."
    )


def manual_sbp_qr_requested_text(order: Order) -> str:
    note = escape(order.moderation_note or order.payment_code)
    return (
        "Спасибо за выбор 💙\n\n"
        "Сейчас формируется QR-код СБП для оплаты.\n\n"
        "Когда QR придёт сюда, оплатите сумму заказа и в назначении/комментарии используйте:\n"
        f"<code>{note}</code>"
    )


def admin_qr_request_text(order: Order) -> str:
    user = order.user
    username = f"@{escape(user.username)}" if user and user.username else "без username"
    note = escape(order.moderation_note or order.payment_code)
    return (
        "🧾 Пользователь выбрал оплату СБП\n\n"
        f"Заказ: <code>{order.id}</code>\n"
        f"Пользователь: {username}\n"
        f"Telegram ID: <code>{user.telegram_id if user else '?'}</code>\n"
        f"Тариф: <b>{escape(order.plan_code)}</b>\n"
        f"Сумма: {format_price(Decimal(order.amount_rub))} RUB\n\n"
        "Слова/комментарий для платежа:\n"
        f"<code>{note}</code>"
    )


def admin_review_request_text(order: Order) -> str:
    user = order.user
    username = f"@{escape(user.username)}" if user and user.username else "без username"
    note = escape(order.moderation_note or order.payment_code)
    return (
        "🔎 Пользователь нажал Проверить оплату\n\n"
        f"Заказ: <code>{order.id}</code>\n"
        f"Пользователь: {username}\n"
        f"Telegram ID: <code>{user.telegram_id if user else '?'}</code>\n"
        f"Тариф: <b>{escape(order.plan_code)}</b>\n"
        f"Сумма: {format_price(Decimal(order.amount_rub))} RUB\n"
        f"Провайдер: <b>{escape(payment_provider_label(order.payment_provider))}</b>\n\n"
        "Код/слова платежа:\n"
        f"<code>{note}</code>\n\n"
        "Проверьте оплату и выберите решение."
    )


def admin_review_orders_menu_text(count: int) -> str:
    if count <= 0:
        return (
            "✅ Очередь чистая\n\n"
            "Заказов, где пользователь уже получил временный доступ и ждёт решения админа, сейчас нет."
        )
    return (
        "🧾 Проверить все заказы\n\n"
        f"На ручной проверке: <b>{count}</b>\n\n"
        "Можно пройти их очередью, скачать txt, вывести весь список сообщениями или загрузить txt "
        "с подтверждёнными кодами для быстрой сверки."
    )


def admin_review_queue_item_text(order: Order, position: int, total: int) -> str:
    return (
        f"📌 Очередь проверки: <b>{position}</b> из <b>{total}</b>\n"
        "Первым идёт тот, кто раньше всех нажал Проверить оплату.\n\n"
        f"{admin_review_request_text(order)}"
    )


def admin_review_fast_prompt_text(count: int) -> str:
    return (
        "⚡ Быстрая проверка\n\n"
        f"В очереди сейчас: <b>{count}</b>\n\n"
        "Пришлите txt-файл или просто сообщение со списком подтверждённых кодов/слов.\n"
        "Каждый код — с новой строки.\n\n"
        "Что есть в списке — бот подтвердит ✅\n"
        "Чего нет в списке — бот отклонит ❌ и напишет пользователю в поддержку."
    )


def admin_review_fast_result_text(confirmed: int, rejected: int, unknown: list[str]) -> str:
    unknown_text = ""
    if unknown:
        visible = "\n".join(f"• <code>{escape(item)}</code>" for item in unknown[:20])
        tail = "\n..." if len(unknown) > 20 else ""
        unknown_text = f"\n\nНе нашёл в очереди:\n{visible}{tail}"
    return (
        "⚡ Быстрая проверка закончена\n\n"
        f"Подтверждено: <b>{confirmed}</b>\n"
        f"Отклонено: <b>{rejected}</b>"
        f"{unknown_text}"
    )


def admin_review_orders_txt(orders: list[Order]) -> str:
    if not orders:
        return "Очередь проверки пуста.\n"

    lines = ["MiloshVPN: очередь проверки заказов", ""]
    for index, order in enumerate(orders, start=1):
        user = order.user
        username = f"@{user.username}" if user and user.username else "без username"
        telegram_id = user.telegram_id if user else "?"
        plan_title = order.plan.title if order.plan else order.plan_code
        note = order.moderation_note or order.payment_code
        paid_at = order.paid_at.strftime("%d.%m.%Y %H:%M UTC") if order.paid_at else "нет"
        expires = order.provisional_expires_at.strftime("%d.%m.%Y %H:%M UTC") if order.provisional_expires_at else "нет"
        lines.extend(
            [
                f"{index}. Заказ #{order.id}",
                f"Пользователь: {username}",
                f"Telegram ID: {telegram_id}",
                f"Тариф: {plan_title} ({order.plan_code})",
                f"Сумма: {format_price(Decimal(order.amount_rub))} RUB",
                f"Провайдер: {payment_provider_label(order.payment_provider)}",
                f"Код: {order.payment_code}",
                f"Код/слова платежа: {note}",
                f"Оплата нажата: {paid_at}",
                f"Временный доступ до: {expires}",
                "",
            ]
        )
    return "\n".join(lines)


def payment_mode_admin_text(active_provider: str) -> str:
    return (
        "💳 Система оплаты\n\n"
        f"Сейчас активна: <b>{escape(payment_provider_label(active_provider))}</b>\n\n"
        "Выберите, как бот будет принимать новые заказы."
    )


def payment_mode_confirm_text(provider: str) -> str:
    return f"Сделать системой оплаты <b>{escape(payment_provider_label(provider))}</b>?"


def payment_mode_applied_text(provider: str) -> str:
    return f"Готово. Теперь новые заказы идут через <b>{escape(payment_provider_label(provider))}</b>."


def moderation_rejected_user_text() -> str:
    return (
        "Извините, ваша оплата не прошла или вы не оплатили заказ.\n\n"
        "Свяжитесь с поддержкой, мы поможем и посмотрим скриншот оплаты. "
        "Если вы оплатили, но забыли вставить код или слова, разберёмся.\n\n"
        "С уважением, MiloshVPN 💙"
    )


def subscription_text(subscription: Subscription | None, key: VpnKey | None) -> str:
    if subscription is None:
        return "Подписки пока нет. Жми <b>Купить</b>, выберем тебе хороший вход."

    traffic = "без лимита" if subscription.traffic_limit_gb is None else f"{subscription.traffic_limit_gb} ГБ"
    title = subscription_title(subscription)
    text = (
        f"💎 Моя подписка: {title}\n\n"
        f"Старт: {subscription.starts_at:%d.%m.%Y %H:%M UTC}\n"
        f"Финиш: {subscription.expires_at:%d.%m.%Y %H:%M UTC}\n"
        "\n"
        f"Лимит: {traffic}"
    )
    if key is not None:
        text += f"\n\nОсталось: {remaining_days_text(subscription)}"
        text += f"\n\n🌍 Сервер: {escape(key.server_label or 'системная 3x-ui')}"
        text += f"\n\n🔑 Sub-ссылка:\n<code>{escape(key.vless_uri)}</code>"
    else:
        text += "\n\nКлюч ещё не выдан. Напиши в поддержку, разберёмся."
    return text


def payment_success_text(subscription: Subscription | None, key: VpnKey | None) -> str:
    return (
        "✅ Оплата прошла!\n\n"
        "Вот ваша sub-ссылка. Спасибо за ваше доверие 💙\n\n"
        f"{subscription_text(subscription, key)}"
    )


def admin_key_text(key: VpnKey) -> str:
    return (
        "Админский ключ создан без оплаты.\n\n"
        f"Label: <code>{escape(key.email)}</code>\n"
        f"Sub-ссылка:\n<code>{escape(key.vless_uri)}</code>"
    )


def admin_reality_key_text(key: VpnKey) -> str:
    return (
        "Админский Reality test key создан без оплаты.\n\n"
        f"Label: <code>{escape(key.email)}</code>\n"
        f"Reality sub-ссылка:\n<code>{escape(key.vless_uri)}</code>"
    )


def admin_order_search_prompt_text() -> str:
    return (
        "🔎 Найти оплату\n\n"
        "Отправь мне код из DonationAlerts, Telegram ID пользователя или ID заказа.\n\n"
        "Примеры:\n"
        "<code>MILO-805074848-55481D</code>\n"
        "<code>805074848</code>\n"
        "<code>/find_order MILO-805074848-55481D</code>"
    )


def admin_order_result_text(order: Order) -> str:
    user = order.user
    username = f"@{escape(user.username)}" if user and user.username else "без username"
    telegram_id = user.telegram_id if user else "?"
    plan_title = order.plan.title if order.plan else order.plan_code
    paid_at = order.paid_at.strftime("%d.%m.%Y %H:%M UTC") if order.paid_at else "ещё нет"
    notified_at = order.notified_at.strftime("%d.%m.%Y %H:%M UTC") if order.notified_at else "ещё нет"
    return (
        "🧾 Заказ найден\n\n"
        f"ID заказа: <code>{order.id}</code>\n"
        f"Пользователь: {username}\n"
        f"Telegram ID: <code>{telegram_id}</code>\n\n"
        f"Тариф: <b>{escape(plan_title)}</b>\n"
        f"Сумма: {format_price(Decimal(order.amount_rub))} RUB\n"
        f"Статус: <b>{escape(order.status)}</b>\n\n"
        f"Код: <code>{escape(order.payment_code)}</code>\n"
        f"Создан: {order.created_at:%d.%m.%Y %H:%M UTC}\n"
        f"Истекает: {order.expires_at:%d.%m.%Y %H:%M UTC}\n"
        f"Оплачен: {paid_at}\n"
        f"Уведомление: {notified_at}"
    )


def admin_manual_grant_result_text(result: ManualOrderGrantResult, notified: bool) -> str:
    if result.granted:
        action = "✅ Заказ вручную отмечен оплаченным, подписка применена, sub создан."
    elif result.repaired_key:
        action = "✅ Заказ уже был оплачен, отсутствующий sub пересоздан."
    elif result.was_already_paid:
        action = "ℹ️ Заказ уже был оплачен, срок повторно не продлевал."
    else:
        action = "ℹ️ Заказ проверен."

    notify_line = "Пользователю отправлено сообщение с sub." if notified else (
        "Сообщение пользователю отправить не получилось. Sub уже в профиле, можно написать ему вручную."
    )
    return (
        f"{action}\n\n"
        f"Заказ: <code>{result.order_id}</code>\n"
        f"Пользователь: <code>{result.user_telegram_id}</code>\n"
        f"Тариф: <b>{escape(result.plan_code)}</b>\n\n"
        f"{notify_line}"
    )


def instruction_text() -> str:
    return (
        "📘 Инструкция\n\n"
        "1. Установи V2RayNG, Hiddify, Streisand или другой клиент с VLESS.\n"
        "\n2. Открой <b>Профиль</b> и скопируй sub-ссылку.\n"
        "\n3. Импортируй ключ из буфера обмена.\n"
        "\n"
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
        "5. Мы не продаём интернет как товар: суммы — это поддержка проекта, чтобы серверы работали стабильно.\n"
        "6. Если ключ начинает вредить серверу, его могут отключить."
    )


def support_text() -> str:
    return (
        "💬 Поддержка\n\n"
        "Что-то не подключилось, оплата потерялась или ключ чудит? Пиши сюда: @miloshadmin"
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
        "• проверить все заказы очередью, txt-файлом или быстрой сверкой;\n"
        "• найти оплату по коду или Telegram ID и выдать ключ вручную;\n"
        "• выбрать систему оплаты для новых заказов;\n"
        "• увидеть личные ключи и удалить лишний;\n"
        "• создать admin key без оплаты;\n"
        "• создать admin Reality test key;\n"
        "• открыть web-страницу direct-node узлов и запустить audit для админского Germany test;\n"
        "• управлять бесплатным публичным ключом.\n\n"
        "<code>/add_admin TELEGRAM_ID</code> — добавить админа.\n"
        "<code>/find_order КОД_ИЛИ_ID</code> — найти оплату.\n"
        "<code>/admin_key</code> — создать admin key.\n"
        "<code>/admin_reality_key</code> — создать admin Reality test key.\n"
        "<code>/admin_direct_node_audit</code> — dry-run direct-node Germany test.\n"
        "<code>/admin_direct_node_create</code> — создать 5 direct-node inbound-ов для админа, если dry-run выключен."
    )


def format_price(value: Decimal) -> str:
    return str(value.quantize(Decimal("1"))) if value == value.quantize(Decimal("1")) else str(value)
