from dataclasses import dataclass
import re

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Referral, User
from app.services.vpn import extend_active_subscription_days
from app.timeutils import utcnow

REFERRAL_REWARD_DAYS = 3
REFERRAL_PAYLOAD_PREFIX = "ref_"
REFERRAL_PAYLOAD_RE = re.compile(r"^ref_([0-9a-z]+)$", re.IGNORECASE)
BASE36_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"


@dataclass(frozen=True)
class ReferralProfile:
    url: str
    credited_count: int


def referral_start_payload(user: User) -> str:
    return f"{REFERRAL_PAYLOAD_PREFIX}{base36_encode(user.id)}"


def referral_url_for_user(user: User, *, bot_username: str | None = None) -> str:
    bot_username = (bot_username or get_settings().bot_username).strip().lstrip("@")
    if not bot_username:
        return ""
    return f"https://t.me/{bot_username}?start={referral_start_payload(user)}"


async def referral_profile_for_user(
    session: AsyncSession,
    user: User,
    *,
    bot_username: str | None = None,
) -> ReferralProfile:
    count = await credited_referrals_count(session, user.id)
    return ReferralProfile(url=referral_url_for_user(user, bot_username=bot_username), credited_count=count)


async def credited_referrals_count(session: AsyncSession, user_id: int) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(Referral)
        .where(Referral.referrer_user_id == user_id, Referral.credited_at.is_not(None))
    )
    return int(count or 0)


async def capture_start_referral(session: AsyncSession, *, referred_user: User, start_payload: str) -> Referral | None:
    referrer_user_id = parse_referrer_user_id(start_payload)
    if referrer_user_id is None or referrer_user_id == referred_user.id:
        return None

    referrer = await session.get(User, referrer_user_id)
    if referrer is None:
        return None

    existing = await session.scalar(select(Referral).where(Referral.referred_user_id == referred_user.id))
    if existing is not None:
        return existing

    referral = Referral(
        referrer_user_id=referrer.id,
        referred_user_id=referred_user.id,
        reward_days=REFERRAL_REWARD_DAYS,
        created_at=utcnow(),
    )
    session.add(referral)
    await session.flush()
    return referral


async def credit_pending_referral(session: AsyncSession, *, referred_user: User) -> Referral | None:
    referral = await session.scalar(
        select(Referral)
        .where(
            Referral.referred_user_id == referred_user.id,
            Referral.credited_at.is_(None),
        )
        .with_for_update()
    )
    if referral is None or referral.referrer_user_id == referred_user.id:
        return None
    if referred_user.channel_gate_completed_at is None:
        return None

    subscription = await extend_active_subscription_days(
        session,
        referral.referrer_user_id,
        referral.reward_days,
    )
    if subscription is None:
        return None

    referral.credited_at = utcnow()
    await session.flush()
    return referral


def parse_referrer_user_id(start_payload: str) -> int | None:
    match = REFERRAL_PAYLOAD_RE.fullmatch(start_payload.strip())
    if match is None:
        return None
    return base36_decode(match.group(1))


def base36_encode(value: int) -> str:
    if value < 0:
        raise ValueError("base36 value must be non-negative")
    if value == 0:
        return "0"

    result = ""
    while value:
        value, index = divmod(value, 36)
        result = BASE36_ALPHABET[index] + result
    return result


def base36_decode(value: str) -> int | None:
    result = 0
    for char in value.lower():
        if char not in BASE36_ALPHABET:
            return None
        result = result * 36 + BASE36_ALPHABET.index(char)
    return result
