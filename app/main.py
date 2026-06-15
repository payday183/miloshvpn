import base64
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import router as admin_router
from app.cache import ping as redis_ping
from app.db import get_session, init_db
from app.models import Order, User
from app.services.billing import poll_donations
from app.services.direct_node_admin import direct_subscription_payload
from app.services.public_keys import get_active_public_key, rotate_public_key
from app.services.stats import collect_stats
from app.services.vpn import get_active_key, get_active_subscription
from app.timeutils import utcnow

app = FastAPI(title="MiloshVPN Control Center")
app.include_router(admin_router)
SUBSCRIPTION_DISPLAY_NAME = "MiloshVPN"


@app.on_event("startup")
async def startup() -> None:
    await init_db()


@app.get("/health")
async def health() -> dict[str, str]:
    redis_status = "ok" if await redis_ping() else "error"
    return {"status": "ok", "redis": redis_status}


@app.get("/api/admin/stats")
async def admin_stats(session: AsyncSession = Depends(get_session)) -> dict[str, int]:
    return await collect_stats(session)


@app.post("/api/admin/poll-donations")
async def poll_donations_now(session: AsyncSession = Depends(get_session)) -> dict[str, int]:
    processed = await poll_donations(session)
    return {"processed": processed}


@app.post("/api/admin/public-key/rotate")
async def rotate_public_key_now(session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    key = await rotate_public_key(session)
    return {"vless_uri": key.vless_uri}


@app.get("/api/public-key")
async def public_key(session: AsyncSession = Depends(get_session)) -> dict[str, str | None]:
    key = await get_active_public_key(session)
    return {"vless_uri": key.vless_uri if key else None}


@app.get("/sub/direct/{token}", response_class=PlainTextResponse)
async def direct_subscription(token: str, session: AsyncSession = Depends(get_session)) -> PlainTextResponse:
    payload = await direct_subscription_payload(session, token)
    if payload is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return PlainTextResponse(payload, headers=subscription_response_headers())


def subscription_response_headers() -> dict[str, str]:
    encoded_title = base64.b64encode(SUBSCRIPTION_DISPLAY_NAME.encode("utf-8")).decode("ascii")
    filename = f"{SUBSCRIPTION_DISPLAY_NAME}.txt"
    return {
        "profile-title": f"base64:{encoded_title}",
        "Content-Disposition": f'attachment; filename="{filename}"; filename*=UTF-8\'\'{quote(filename)}',
    }


@app.get("/api/users/{telegram_id}")
async def user_profile(telegram_id: int, session: AsyncSession = Depends(get_session)) -> dict[str, object]:
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    subscription = await get_active_subscription(session, user.id)
    key = await get_active_key(session, user.id)
    pending_order = await session.scalar(
        select(Order)
        .where(Order.user_id == user.id, Order.status == "pending", Order.expires_at > utcnow())
        .order_by(Order.created_at.desc())
    )
    return {
        "telegram_id": user.telegram_id,
        "username": user.username,
        "role": user.role,
        "subscription": {
            "active": subscription is not None,
            "plan": subscription.plan_code if subscription else None,
            "expires_at": subscription.expires_at.isoformat() if subscription else None,
        },
        "vpn_key": key.vless_uri if key else None,
        "pending_payment_code": pending_order.payment_code if pending_order else None,
    }
