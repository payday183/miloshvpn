import asyncio
import logging

from aiogram import Bot
import httpx

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.services.billing import poll_donations
from app.services.expiry import expire_subscriptions, retry_expired_key_revokes
from app.services.payment_notifications import notify_paid_orders
from app.services.payment_moderation import expire_unconfirmed_orders
from app.services.public_keys import (
    expire_public_keys,
    mark_public_key_posted,
    normalize_public_key_chat_id,
    public_key_channel_post_text,
    rotate_public_key,
    seconds_until_next_public_key_post,
)
from app.services.system_x3ui import collect_system_x3ui_state
from app.services.x3ui import X3UIError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

DONATION_AUTH_RETRY_SECONDS = 15 * 60


async def donation_loop() -> None:
    settings = get_settings()
    bot = Bot(settings.bot_token) if settings.bot_token else None
    while True:
        sleep_seconds = settings.donationalerts_poll_interval_seconds
        async with SessionLocal() as session:
            try:
                processed = await poll_donations(session)
                if processed:
                    logger.info("Processed donations: %s", processed)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in {401, 403}:
                    sleep_seconds = max(sleep_seconds, DONATION_AUTH_RETRY_SECONDS)
                    logger.warning(
                        "DonationAlerts auth failed with %s; retrying in %s seconds",
                        exc.response.status_code,
                        sleep_seconds,
                    )
                else:
                    logger.exception("Donation polling failed")
            except Exception:
                logger.exception("Donation polling failed")
        if bot is not None:
            try:
                notified = await notify_paid_orders(bot)
                if notified:
                    logger.info("Notified paid orders: %s", notified)
            except Exception:
                logger.exception("Paid order notifications failed")
        await asyncio.sleep(sleep_seconds)


async def public_key_loop() -> None:
    settings = get_settings()
    chat_id = normalize_public_key_chat_id(settings.public_key_chat_id)
    bot = Bot(settings.bot_token) if settings.bot_token and chat_id else None
    if settings.public_key_enabled and bot is None:
        logger.warning("Public key posting is enabled, but BOT_TOKEN or PUBLIC_KEY_CHAT_ID is empty")

    while True:
        sleep_seconds = 300
        if settings.public_key_enabled:
            async with SessionLocal() as session:
                try:
                    seconds_left = await seconds_until_next_public_key_post(session)
                    if seconds_left <= 0:
                        if bot is None:
                            logger.warning("Skipping public key post because Telegram target is not configured")
                        else:
                            key = await rotate_public_key(session)
                            text = await public_key_channel_post_text(session, key)
                            await bot.send_message(chat_id, text, parse_mode="HTML")
                            await mark_public_key_posted(session)
                            logger.info("Published public VPN key: %s", key.email)
                            sleep_seconds = max(60, await seconds_until_next_public_key_post(session))
                    else:
                        sleep_seconds = max(60, seconds_left)
                except X3UIError as exc:
                    logger.warning("Skipping public key rotation: %s", exc)
                    sleep_seconds = max(300, settings.node_status_poll_interval_seconds)
                except Exception:
                    logger.exception("Public key rotation failed")
        await asyncio.sleep(sleep_seconds)


async def node_status_loop() -> None:
    settings = get_settings()
    while True:
        try:
            state = await collect_system_x3ui_state()
            logger.info(
                "System 3x-ui state: status=%s nodes=%s inbounds=%s error=%s",
                state.get("status"),
                len(state.get("nodes") or []),
                len(state.get("inbounds") or []),
                state.get("error") or "",
            )
        except Exception:
            logger.exception("System 3x-ui status polling failed")
        await asyncio.sleep(settings.node_status_poll_interval_seconds)


async def expired_subscription_loop() -> None:
    settings = get_settings()
    while True:
        if settings.expired_subscription_cleanup_enabled:
            async with SessionLocal() as session:
                try:
                    expired = await expire_subscriptions(session)
                    retry = await retry_expired_key_revokes(session)
                    unconfirmed = await expire_unconfirmed_orders(session)
                    public = await expire_public_keys(session)
                    total_revoked = expired["revoked_keys"] + retry["revoked_keys"]
                    total_failed = expired["failed_revokes"] + retry["failed_revokes"] + public["failed_revokes"]
                    if (
                        expired["expired_subscriptions"]
                        or unconfirmed
                        or public["revoked_keys"]
                        or total_revoked
                        or total_failed
                    ):
                        logger.info(
                            "Expired subscriptions cleanup: expired=%s unconfirmed=%s public=%s revoked=%s failed=%s",
                            expired["expired_subscriptions"],
                            unconfirmed,
                            public["revoked_keys"],
                            total_revoked,
                            total_failed,
                        )
                except Exception:
                    logger.exception("Expired subscriptions cleanup failed")
        await asyncio.sleep(settings.expired_subscription_cleanup_interval_seconds)


async def main() -> None:
    await init_db()
    await asyncio.gather(
        donation_loop(),
        public_key_loop(),
        node_status_loop(),
        expired_subscription_loop(),
    )


if __name__ == "__main__":
    asyncio.run(main())
