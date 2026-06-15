from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.timeutils import utcnow

ACTION_COLUMNS = {
    "buy": ("buy_clicks", "last_buy_clicked_at"),
    "support": ("support_clicks", "last_support_clicked_at"),
    "replace_key": ("replace_key_clicks", "last_replace_key_clicked_at"),
    "replace_key_success": ("replace_key_successes", "last_replace_key_completed_at"),
}


async def record_user_action(session: AsyncSession, user: User, action: str) -> None:
    columns = ACTION_COLUMNS.get(action)
    if columns is None:
        raise ValueError(f"Unknown user action: {action}")

    counter_column, timestamp_column = columns
    setattr(user, counter_column, int(getattr(user, counter_column) or 0) + 1)
    setattr(user, timestamp_column, utcnow())
    await session.flush()
