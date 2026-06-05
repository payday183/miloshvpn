import asyncio
import logging

from aiogram import Bot

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.services.billing import poll_donations
from app.services.node_monitor import refresh_all_nodes
from app.services.public_keys import get_active_public_key, public_key_post_text, rotate_public_key
from app.timeutils import utcnow

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def donation_loop() -> None:
    settings = get_settings()
    while True:
        async with SessionLocal() as session:
            try:
                processed = await poll_donations(session)
                if processed:
                    logger.info("Processed donations: %s", processed)
            except Exception:
                logger.exception("Donation polling failed")
        await asyncio.sleep(settings.donationalerts_poll_interval_seconds)


async def public_key_loop() -> None:
    settings = get_settings()
    bot = Bot(settings.bot_token) if settings.bot_token and settings.public_key_chat_id else None
    while True:
        if settings.public_key_enabled:
            async with SessionLocal() as session:
                try:
                    key = await get_active_public_key(session)
                    should_rotate = key is None or (
                        key.expires_at is not None and key.expires_at <= utcnow()
                    )
                    if should_rotate:
                        key = await rotate_public_key(session)
                        logger.info("Rotated public VPN key: %s", key.email)
                        if bot is not None:
                            await bot.send_message(
                                settings.public_key_chat_id,
                                public_key_post_text(key),
                                parse_mode="HTML",
                            )
                except Exception:
                    logger.exception("Public key rotation failed")
        await asyncio.sleep(300)


async def node_status_loop() -> None:
    settings = get_settings()
    while True:
        async with SessionLocal() as session:
            try:
                nodes = await refresh_all_nodes(session)
                if nodes:
                    logger.info("Refreshed VPN nodes: %s", len(nodes))
            except Exception:
                logger.exception("Node status polling failed")
        await asyncio.sleep(settings.node_status_poll_interval_seconds)


async def main() -> None:
    await init_db()
    await asyncio.gather(donation_loop(), public_key_loop(), node_status_loop())


if __name__ == "__main__":
    asyncio.run(main())
