from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import get_json, set_json
from app.models import Order, Subscription, User, VpnKey
from app.timeutils import utcnow


async def collect_stats(session: AsyncSession) -> dict[str, int]:
    cached = await get_json("stats:v1")
    if cached is not None:
        return {key: int(value) for key, value in cached.items()}

    now = utcnow()
    users = await session.scalar(select(func.count()).select_from(User))
    active_subscriptions = await session.scalar(
        select(func.count())
        .select_from(Subscription)
        .where(Subscription.status == "active", Subscription.expires_at > now)
    )
    paid_orders = await session.scalar(select(func.count()).select_from(Order).where(Order.status == "paid"))
    pending_orders = await session.scalar(select(func.count()).select_from(Order).where(Order.status == "pending"))
    active_keys = await session.scalar(select(func.count()).select_from(VpnKey).where(VpnKey.active.is_(True)))
    stats = {
        "users": int(users or 0),
        "active_subscriptions": int(active_subscriptions or 0),
        "paid_orders": int(paid_orders or 0),
        "pending_orders": int(pending_orders or 0),
        "active_keys": int(active_keys or 0),
    }
    await set_json("stats:v1", stats, ttl_seconds=15)
    return stats
