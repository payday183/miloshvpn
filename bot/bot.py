from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher

try:
    from bot.config import settings
    from bot.routers import setup
except ModuleNotFoundError:
    from config import settings
    from routers import setup


bot = Bot(token=settings.bot_token)
dp = Dispatcher()

setup(dp)


async def main() -> None:
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
