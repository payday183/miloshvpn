from datetime import datetime, timedelta
from html import escape
from math import ceil
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import PublicKeyPostTemplate, Setting, VpnKey
from app.services.system_x3ui import select_inbounds_for_client
from app.services.x3ui import X3UIClient
from app.timeutils import utcnow

PUBLIC_KEY_LAST_POSTED_AT = "public_key_last_posted_at"
PUBLIC_KEY_TEMPLATE_INDEX = "public_key_template_index"
PUBLIC_KEY_LIFETIME_HOURS = 24
PUBLIC_KEY_POST_INTERVAL_HOURS = 48
PUBLIC_KEY_TRAFFIC_GB = 500
PUBLIC_KEY_TIMEZONE = ZoneInfo("Europe/Moscow")


async def get_active_public_key(session: AsyncSession) -> VpnKey | None:
    now = utcnow()
    return await session.scalar(
        select(VpnKey)
        .where(
            VpnKey.key_type == "public",
            VpnKey.active.is_(True),
            (VpnKey.expires_at.is_(None)) | (VpnKey.expires_at > now),
        )
        .order_by(VpnKey.created_at.desc())
    )


async def rotate_public_key(session: AsyncSession) -> VpnKey:
    settings = get_settings()
    now = utcnow()
    lifetime_hours = _public_key_lifetime_hours(settings)
    traffic_gb = _public_key_traffic_gb(settings)
    selection = await select_inbounds_for_client("public")

    keys = (
        await session.scalars(
            select(VpnKey)
            .where(VpnKey.key_type == "public", VpnKey.active.is_(True))
        )
    ).all()
    for key in keys:
        x3ui = X3UIClient(settings)
        await x3ui.revoke_client(client_uuid=key.x3ui_client_uuid, email=key.email)
        key.active = False
        key.revoked_at = now

    expires_at = now + timedelta(hours=lifetime_hours)
    x3ui = X3UIClient(settings)
    client = await x3ui.create_subscription_client(
        email=f"milosh_free_{now:%Y%m%d_%H%M}",
        telegram_id=None,
        expires_at=expires_at,
        traffic_gb=traffic_gb,
        inbound_ids=selection.inbound_ids,
    )
    key = VpnKey(
        node_id=None,
        user_id=None,
        subscription_id=None,
        key_type="public",
        x3ui_client_uuid=client.client_uuid,
        email=client.email,
        vless_uri=client.vless_uri,
        active=True,
        created_at=now,
        expires_at=expires_at,
    )
    session.add(key)
    await session.commit()
    await session.refresh(key)
    return key


async def seconds_until_next_public_key_post(session: AsyncSession, now: datetime | None = None) -> int:
    settings = get_settings()
    if not settings.public_key_enabled:
        return 300

    now = now or utcnow()
    active_key = await get_active_public_key(session)
    last_posted_at = await get_public_key_last_posted_at(session)

    if active_key is None:
        if last_posted_at is None:
            return 0
        next_post_at = _next_public_key_post_at(
            last_posted_at=last_posted_at,
            post_hour_msk=settings.public_key_post_hour_msk,
            interval_hours=_public_key_post_interval_hours(settings),
        )
        return max(0, ceil((next_post_at - now).total_seconds()))

    if last_posted_at is None:
        next_post_at = _scheduled_public_key_post_at(now, settings.public_key_post_hour_msk)
    else:
        next_post_at = _next_public_key_post_at(
            last_posted_at=last_posted_at,
            post_hour_msk=settings.public_key_post_hour_msk,
            interval_hours=_public_key_post_interval_hours(settings),
        )

    return max(0, ceil((next_post_at - now).total_seconds()))


async def expire_public_keys(session: AsyncSession, *, limit: int = 100) -> dict[str, int]:
    now = utcnow()
    keys = (
        await session.scalars(
            select(VpnKey)
            .where(
                VpnKey.key_type == "public",
                VpnKey.active.is_(True),
                VpnKey.expires_at <= now,
            )
            .order_by(VpnKey.expires_at)
            .limit(limit)
        )
    ).all()

    revoked_keys = 0
    failed_revokes = 0
    for key in keys:
        try:
            await X3UIClient().revoke_client(client_uuid=key.x3ui_client_uuid, email=key.email)
        except Exception:
            failed_revokes += 1
            continue

        key.active = False
        key.revoked_at = now
        revoked_keys += 1

    await session.commit()
    return {"revoked_keys": revoked_keys, "failed_revokes": failed_revokes}


async def get_public_key_last_posted_at(session: AsyncSession) -> datetime | None:
    value = await get_setting_value(session, PUBLIC_KEY_LAST_POSTED_AT)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=utcnow().tzinfo)


async def mark_public_key_posted(session: AsyncSession, posted_at: datetime | None = None) -> None:
    posted_at = posted_at or utcnow()
    index = await get_setting_int(session, PUBLIC_KEY_TEMPLATE_INDEX)
    await set_setting_value(session, PUBLIC_KEY_LAST_POSTED_AT, posted_at.isoformat())
    await set_setting_value(session, PUBLIC_KEY_TEMPLATE_INDEX, str(index + 1))
    await session.commit()


async def public_key_channel_post_text(session: AsyncSession, key: VpnKey) -> str:
    template = await next_public_key_post_template(session)
    return public_key_post_text(key, template.body if template else None)


async def next_public_key_post_template(session: AsyncSession) -> PublicKeyPostTemplate | None:
    templates = (
        await session.scalars(
            select(PublicKeyPostTemplate)
            .where(PublicKeyPostTemplate.is_active.is_(True))
            .order_by(PublicKeyPostTemplate.id)
        )
    ).all()
    if not templates:
        return None

    index = await get_setting_int(session, PUBLIC_KEY_TEMPLATE_INDEX)
    return templates[index % len(templates)]


def normalize_public_key_chat_id(raw: str) -> str:
    value = raw.strip()
    if not value:
        return ""
    if value.startswith("@") or value.lstrip("-").isdigit():
        return value

    parsed = urlsplit(value)
    if parsed.netloc.lower() in {"t.me", "telegram.me", "www.t.me", "www.telegram.me"}:
        username = parsed.path.strip("/").split("/", 1)[0]
        if username and not username.startswith(("+", "joinchat")):
            return f"@{username}"

    return value


async def get_setting_value(session: AsyncSession, key: str) -> str | None:
    setting = await session.scalar(select(Setting).where(Setting.key == key))
    return setting.value if setting is not None else None


async def get_setting_int(session: AsyncSession, key: str) -> int:
    value = await get_setting_value(session, key)
    if value is None:
        return 0
    try:
        return int(value)
    except ValueError:
        return 0


async def set_setting_value(session: AsyncSession, key: str, value: str) -> None:
    setting = await session.scalar(select(Setting).where(Setting.key == key))
    if setting is None:
        session.add(Setting(key=key, value=value))
    else:
        setting.value = value


def public_key_post_text(key: VpnKey, template_body: str | None = None) -> str:
    settings = get_settings()
    lifetime_hours = _public_key_lifetime_hours(settings)
    post_interval_hours = _public_key_post_interval_hours(settings)
    traffic_gb = _public_key_traffic_gb(settings)
    expires = (
        key.expires_at.astimezone(PUBLIC_KEY_TIMEZONE).strftime("%d.%m %H:%M MSK")
        if key.expires_at
        else f"через {lifetime_hours} часа"
    )
    next_free_key_at = _next_public_key_post_at(
        last_posted_at=key.created_at,
        post_hour_msk=settings.public_key_post_hour_msk,
        interval_hours=post_interval_hours,
    ).astimezone(PUBLIC_KEY_TIMEZONE).strftime("%d.%m %H:%M MSK")
    vless_uri = escape(key.vless_uri)
    context = {
        "expires": expires,
        "next_free_key_at": next_free_key_at,
        "hours": str(lifetime_hours),
        "traffic_gb": str(traffic_gb),
        "key": vless_uri,
        "key_block": f"<code>{vless_uri}</code>",
    }
    if template_body:
        try:
            return template_body.format(**context)
        except (KeyError, ValueError):
            pass

    return (
        "Бесплатный ключ MiloshVPN уже на столе.\n\n"
        "Забирай sub-ссылку, проверяй скорость и не рассказывай интернету, что он был медленным.\n\n"
        f"{context['key_block']}\n\n"
        f"Живет до: {expires}\n"
        f"Лимит: {context['traffic_gb']} ГБ\n"
        f"Следующий бесплатный sub: {context['next_free_key_at']}."
    )


def _public_key_lifetime_hours(settings: object) -> int:
    value = int(getattr(settings, "public_key_rotate_hours", PUBLIC_KEY_LIFETIME_HOURS) or PUBLIC_KEY_LIFETIME_HOURS)
    return max(1, value)


def _public_key_post_interval_hours(settings: object) -> int:
    value = int(
        getattr(settings, "public_key_post_interval_hours", PUBLIC_KEY_POST_INTERVAL_HOURS)
        or PUBLIC_KEY_POST_INTERVAL_HOURS
    )
    return max(1, value)


def _public_key_traffic_gb(settings: object) -> int:
    value = int(getattr(settings, "public_key_traffic_gb", PUBLIC_KEY_TRAFFIC_GB) or PUBLIC_KEY_TRAFFIC_GB)
    return max(1, value)


def _public_key_post_hour_msk(raw_hour: int) -> int:
    return max(0, min(23, int(raw_hour)))


def _scheduled_public_key_post_at(now: datetime, post_hour_msk: int) -> datetime:
    now_msk = now.astimezone(PUBLIC_KEY_TIMEZONE)
    return now_msk.replace(
        hour=_public_key_post_hour_msk(post_hour_msk),
        minute=0,
        second=0,
        microsecond=0,
    ).astimezone(now.tzinfo)


def _next_public_key_post_at(*, last_posted_at: datetime, post_hour_msk: int, interval_hours: int) -> datetime:
    last_posted_msk = last_posted_at.astimezone(PUBLIC_KEY_TIMEZONE)
    candidate = last_posted_msk.replace(
        hour=_public_key_post_hour_msk(post_hour_msk),
        minute=0,
        second=0,
        microsecond=0,
    )
    if candidate <= last_posted_msk:
        candidate += timedelta(hours=max(1, interval_hours))
    return candidate.astimezone(last_posted_at.tzinfo)
