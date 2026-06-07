from datetime import datetime, timedelta
from html import escape
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.models import PublicKeyPostTemplate, Setting, VpnKey
from app.services.nodes import select_node_for_key
from app.services.x3ui import X3UIClient
from app.timeutils import utcnow

PUBLIC_KEY_LAST_POSTED_AT = "public_key_last_posted_at"
PUBLIC_KEY_TEMPLATE_INDEX = "public_key_template_index"
PUBLIC_KEY_LIFETIME_HOURS = 24
PUBLIC_KEY_TRAFFIC_GB = 500


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
    node = await select_node_for_key(session)

    keys = (
        await session.scalars(
            select(VpnKey)
            .options(selectinload(VpnKey.node))
            .where(VpnKey.key_type == "public", VpnKey.active.is_(True))
        )
    ).all()
    for key in keys:
        x3ui = X3UIClient(settings, node=key.node)
        await x3ui.revoke_client(client_uuid=key.x3ui_client_uuid, email=key.email)
        key.active = False
        key.revoked_at = now

    expires_at = now + timedelta(hours=PUBLIC_KEY_LIFETIME_HOURS)
    x3ui = X3UIClient(settings, node=node)
    client = await x3ui.create_client(
        email=f"milosh_free_{now:%Y%m%d_%H%M}",
        telegram_id=None,
        expires_at=expires_at,
        traffic_gb=PUBLIC_KEY_TRAFFIC_GB,
    )
    key = VpnKey(
        node_id=node.id if node else None,
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
    if active_key is None:
        return 0

    last_posted_at = await get_public_key_last_posted_at(session)
    if last_posted_at is None:
        return 0

    next_post_at = last_posted_at + timedelta(hours=PUBLIC_KEY_LIFETIME_HOURS)
    return max(0, int((next_post_at - now).total_seconds()))


async def expire_public_keys(session: AsyncSession, *, limit: int = 100) -> dict[str, int]:
    now = utcnow()
    keys = (
        await session.scalars(
            select(VpnKey)
            .options(selectinload(VpnKey.node))
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
            await X3UIClient(node=key.node).revoke_client(client_uuid=key.x3ui_client_uuid, email=key.email)
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
    expires = key.expires_at.strftime("%d.%m %H:%M UTC") if key.expires_at else "через 24 часа"
    vless_uri = escape(key.vless_uri)
    context = {
        "expires": expires,
        "hours": str(PUBLIC_KEY_LIFETIME_HOURS),
        "traffic_gb": str(PUBLIC_KEY_TRAFFIC_GB),
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
        "Забирай VLESS, проверяй скорость и не рассказывай интернету, что он был медленным.\n\n"
        f"{context['key_block']}\n\n"
        f"Живет до: {expires}\n"
        f"Лимит: {context['traffic_gb']} ГБ\n"
        f"Через {context['hours']} ч ключ будет заменен автоматически."
    )
