from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import BotAdmin, Plan, PublicKeyPostTemplate, VpnNode
from app.timeutils import utcnow


DEFAULT_PLANS = [
    {
        "code": "basic",
        "title": "Базовый тариф",
        "description": "30 дней, лимит 500 ГБ. Нормальный вход в MiloshVPN без лишнего пафоса.",
        "price_rub": Decimal("75.00"),
        "days": 30,
        "traffic_gb": 500,
    },
    {
        "code": "unlimited",
        "title": "Без ограничений",
        "description": "30 дней, без лимита трафика. Интернет без поводка.",
        "price_rub": Decimal("95.00"),
        "days": 30,
        "traffic_gb": None,
    },
]

TRIAL_PLAN_CODE = "trial"

PUBLIC_KEY_POST_TEMPLATES = [
    {
        "code": "free_key_01",
        "title": "Свежий ключ на столе",
        "body": (
            "🔥 Свежий бесплатный ключ MiloshVPN уже на столе.\n\n"
            "Забирай, тестируй Telegram, YouTube, Instagram и обычные сайты.\n\n"
            "{key_block}\n\n"
            "⏳ Живёт до: {expires}\n\n"
            "📦 Лимит: {traffic_gb} ГБ\n\n"
            "Через {hours} ч ключ заменится автоматически."
        ),
    },
    {
        "code": "free_key_02",
        "title": "Интернет без лишних танцев",
        "body": (
            "⚡ MiloshVPN подкинул бесплатный VLESS на сегодня.\n\n"
            "Без лишних танцев: скопировал, вставил в клиент, подключился.\n\n"
            "{key_block}\n\n"
            "⏳ До: {expires}\n\n"
            "📦 Трафик: {traffic_gb} ГБ"
        ),
    },
    {
        "code": "free_key_03",
        "title": "Проверочный вход",
        "body": (
            "😎 Дорогой друг, вот бесплатный вход в MiloshVPN на сутки.\n\n"
            "Можно спокойно проверить скорость и белые списки.\n\n"
            "{key_block}\n\n"
            "⏳ Активен до: {expires}\n\n"
            "📦 Лимит: {traffic_gb} ГБ"
        ),
    },
    {
        "code": "free_key_04",
        "title": "Ключ дня",
        "body": (
            "🚀 Ключ дня подъехал.\n\n"
            "Берёшь VLESS, импортируешь в клиент и смотришь, как интернет становится приятнее.\n\n"
            "{key_block}\n\n"
            "⏳ Работает до: {expires}\n\n"
            "📦 Лимит на сутки: {traffic_gb} ГБ"
        ),
    },
    {
        "code": "free_key_05",
        "title": "Без душноты",
        "body": (
            "✨ Бесплатный MiloshVPN без душноты и длинных инструкций.\n\n"
            "Ключ ниже, копируй целиком:\n\n"
            "{key_block}\n\n"
            "⏳ До: {expires}\n\n"
            "📦 {traffic_gb} ГБ на тест"
        ),
    },
    {
        "code": "free_key_06",
        "title": "Суточный тест",
        "body": (
            "🛡 Суточный тест MiloshVPN открыт.\n\n"
            "Подключайся и проверяй нужные сервисы без лишней суеты.\n\n"
            "{key_block}\n\n"
            "⏳ Финиш: {expires}\n\n"
            "📦 Лимит: {traffic_gb} ГБ"
        ),
    },
    {
        "code": "free_key_07",
        "title": "Подарок каналу",
        "body": (
            "🎁 Подарок каналу: бесплатный VLESS-ключ на 24 часа.\n\n"
            "Если давно хотел попробовать MiloshVPN, сейчас самое время.\n\n"
            "{key_block}\n\n"
            "⏳ До: {expires}\n\n"
            "📦 Трафик: {traffic_gb} ГБ"
        ),
    },
    {
        "code": "free_key_08",
        "title": "Спокойный доступ",
        "body": (
            "💎 Бесплатный ключ MiloshVPN готов.\n\n"
            "Для тех, кто хочет просто нормальный доступ, без странных плясок вокруг интернета.\n\n"
            "{key_block}\n\n"
            "⏳ Действует до: {expires}\n\n"
            "📦 Лимит: {traffic_gb} ГБ"
        ),
    },
    {
        "code": "free_key_09",
        "title": "Проверка связи",
        "body": (
            "📡 Проверка связи: новый бесплатный ключ уже здесь.\n\n"
            "Копируй VLESS, импортируй в клиент и погнали.\n\n"
            "{key_block}\n\n"
            "⏳ До: {expires}\n\n"
            "📦 На сегодня: {traffic_gb} ГБ"
        ),
    },
    {
        "code": "free_key_10",
        "title": "Красивый тест",
        "body": (
            "🌙 На сегодня у нас красивый тест MiloshVPN.\n\n"
            "Ключ живёт сутки, лимит щедрый, дальше система сама заменит его новым.\n\n"
            "{key_block}\n\n"
            "⏳ До: {expires}\n\n"
            "📦 Лимит: {traffic_gb} ГБ"
        ),
    },
]


async def seed_defaults(session: AsyncSession) -> None:
    settings = get_settings()
    for payload in DEFAULT_PLANS:
        plan = await session.get(Plan, payload["code"])
        if plan is None:
            session.add(Plan(**payload, is_active=True))
        else:
            plan.title = payload["title"]
            plan.description = payload["description"]
            plan.price_rub = payload["price_rub"]
            plan.days = payload["days"]
            plan.traffic_gb = payload["traffic_gb"]
            plan.is_active = True

    trial_payload = {
        "code": TRIAL_PLAN_CODE,
        "title": "Бесплатный 7-дневный ключ",
        "description": "Автоматический trial-доступ после старта бота.",
        "price_rub": Decimal("0.00"),
        "days": settings.free_trial_days,
        "traffic_gb": settings.free_trial_traffic_gb,
    }
    trial_plan = await session.get(Plan, TRIAL_PLAN_CODE)
    if trial_plan is None:
        session.add(Plan(**trial_payload, is_active=False))
    else:
        trial_plan.title = trial_payload["title"]
        trial_plan.description = trial_payload["description"]
        trial_plan.price_rub = trial_payload["price_rub"]
        trial_plan.days = trial_payload["days"]
        trial_plan.traffic_gb = trial_payload["traffic_gb"]
        trial_plan.is_active = False

    for telegram_id in settings.admin_ids:
        existing = await session.scalar(select(BotAdmin).where(BotAdmin.telegram_id == telegram_id))
        if existing is None:
            session.add(BotAdmin(telegram_id=telegram_id, added_at=utcnow()))

    now = utcnow()
    for payload in PUBLIC_KEY_POST_TEMPLATES:
        template = await session.scalar(
            select(PublicKeyPostTemplate).where(PublicKeyPostTemplate.code == payload["code"])
        )
        if template is None:
            session.add(PublicKeyPostTemplate(**payload, is_active=True, created_at=now))
        else:
            template.title = payload["title"]
            template.body = payload["body"]
            template.is_active = True

    node_count = await session.scalar(select(func.count()).select_from(VpnNode))
    if not node_count:
        session.add(
            VpnNode(
                title="Local test 3x-ui",
                mode=settings.x3ui_mode,
                base_url=settings.x3ui_base_url,
                username=settings.x3ui_username,
                password=settings.x3ui_password,
                inbound_id=settings.x3ui_inbound_id,
                max_clients=settings.x3ui_max_clients,
                public_host=settings.vless_public_host,
                public_port=settings.vless_public_port,
                vless_query=settings.vless_query,
                is_active=True,
                status="unknown",
                created_at=now,
                updated_at=now,
            )
        )

    await session.commit()
