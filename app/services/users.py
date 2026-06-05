from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import BotAdmin, User
from app.timeutils import utcnow


async def get_or_create_user(
    session: AsyncSession,
    *,
    telegram_id: int,
    username: str | None,
    first_name: str | None,
) -> User:
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is not None:
        user.username = username
        user.first_name = first_name
        await session.flush()
        return user

    role = "user"
    settings = get_settings()
    admin_count = await session.scalar(select(func.count()).select_from(BotAdmin))
    if telegram_id in settings.admin_ids or (settings.allow_first_admin and admin_count == 0):
        role = "admin"

    user = User(
        telegram_id=telegram_id,
        username=username,
        first_name=first_name,
        role=role,
        created_at=utcnow(),
    )
    session.add(user)
    await session.flush()

    if role == "admin":
        existing = await session.scalar(select(BotAdmin).where(BotAdmin.telegram_id == telegram_id))
        if existing is None:
            session.add(BotAdmin(telegram_id=telegram_id, added_at=utcnow()))
            await session.flush()

    return user


async def is_admin(session: AsyncSession, telegram_id: int) -> bool:
    admin = await session.scalar(select(BotAdmin).where(BotAdmin.telegram_id == telegram_id))
    return admin is not None


async def add_admin(session: AsyncSession, telegram_id: int) -> None:
    existing = await session.scalar(select(BotAdmin).where(BotAdmin.telegram_id == telegram_id))
    if existing is None:
        session.add(BotAdmin(telegram_id=telegram_id, added_at=utcnow()))

    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is not None:
        user.role = "admin"

    await session.commit()
