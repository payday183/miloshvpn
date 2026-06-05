from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Subscription, VpnKey
from app.services.x3ui import X3UIClient
from app.timeutils import utcnow


async def expire_subscriptions(session: AsyncSession, *, limit: int = 200) -> dict[str, int]:
    now = utcnow()
    subscriptions = (
        await session.scalars(
            select(Subscription)
            .options(selectinload(Subscription.keys).selectinload(VpnKey.node))
            .where(Subscription.status == "active", Subscription.expires_at <= now)
            .order_by(Subscription.expires_at)
            .limit(limit)
        )
    ).all()

    expired_subscriptions = 0
    revoked_keys = 0
    failed_revokes = 0

    for subscription in subscriptions:
        subscription.status = "expired"
        expired_subscriptions += 1

        for key in subscription.keys:
            if not key.active:
                continue
            try:
                await X3UIClient(node=key.node).revoke_client(client_uuid=key.x3ui_client_uuid, email=key.email)
            except Exception:
                # Keep the key active so the next cleanup pass retries the 3x-ui deletion.
                failed_revokes += 1
                continue

            key.active = False
            key.revoked_at = now
            revoked_keys += 1

    await session.commit()
    return {
        "expired_subscriptions": expired_subscriptions,
        "revoked_keys": revoked_keys,
        "failed_revokes": failed_revokes,
    }


async def retry_expired_key_revokes(session: AsyncSession, *, limit: int = 200) -> dict[str, int]:
    now = utcnow()
    keys = (
        await session.scalars(
            select(VpnKey)
            .options(selectinload(VpnKey.node), selectinload(VpnKey.subscription))
            .where(
                VpnKey.active.is_(True),
                VpnKey.key_type == "private",
                VpnKey.expires_at <= now,
            )
            .order_by(VpnKey.expires_at)
            .limit(limit)
        )
    ).all()

    revoked_keys = 0
    failed_revokes = 0
    for key in keys:
        if key.subscription is not None and key.subscription.status == "active":
            key.subscription.status = "expired"

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
