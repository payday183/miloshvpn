import logging

from aiogram import Bot
from aiogram.enums import ParseMode

from app.models import VpnKey
from app.tg.texts import admin_reality_key_text

logger = logging.getLogger(__name__)


async def send_admin_reality_key(bot: Bot, telegram_id: int, key: VpnKey) -> bool:
    try:
        await bot.send_message(
            telegram_id,
            admin_reality_key_text(key),
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        logger.exception("Failed to send admin Reality test key %s to %s", key.id, telegram_id)
        return False
    return True
