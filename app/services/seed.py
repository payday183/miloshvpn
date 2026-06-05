from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import BotAdmin, Plan, VpnNode
from app.timeutils import utcnow


DEFAULT_PLANS = [
    {
        "code": "basic",
        "title": "Дешевый тариф",
        "description": "30 дней, лимит 50 ГБ. Нормальный вход в MiloshVPN без лишнего пафоса.",
        "price_rub": Decimal("75.00"),
        "days": 30,
        "traffic_gb": 50,
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


async def seed_defaults(session: AsyncSession) -> None:
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

    settings = get_settings()
    for telegram_id in settings.admin_ids:
        existing = await session.scalar(select(BotAdmin).where(BotAdmin.telegram_id == telegram_id))
        if existing is None:
            session.add(BotAdmin(telegram_id=telegram_id, added_at=utcnow()))

    node_count = await session.scalar(select(func.count()).select_from(VpnNode))
    if not node_count:
        now = utcnow()
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
                created_at=now,
                updated_at=now,
            )
        )

    await session.commit()
