import base64
import json
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
HAPP_ROUTING_PROFILE = {
    "Name": "MiloshVPN — RU direct + abuse protection",
    "GlobalProxy": "true",
    "RemoteDNSType": "DoU",
    "RemoteDNSDomain": "",
    "RemoteDNSIP": "1.1.1.3",
    "DomesticDNSType": "DoU",
    "DomesticDNSDomain": "",
    "DomesticDNSIP": "77.88.8.7",
    "Geoipurl": "",
    "Geositeurl": "",
    "LastUpdated": "",
    "DnsHosts": {},
    "DirectSites": [
        # Russian zones are always reached outside the VPN.  Explicit domains
        # cover banking/payment and Russian app backends hosted under generic
        # TLDs, while keeping the profile independent from geosite.dat.
        "regexp:.*\\.(ru|su|xn--p1ai)$",
        "domain:sber.com",
        "domain:sberbank.com",
        "domain:tbank.com",
        "domain:tinkoff.com",
        "domain:tinkoff-group.com",
        "domain:alfabank.com",
        "domain:vtb.com",
        "domain:gazprombank.com",
        "domain:raiffeisen.com",
        "domain:home.bank",
        "domain:qiwi.com",
        "domain:koronapay.com",
        "domain:unistream.com",
        "domain:robokassa.com",
        "domain:payselection.com",
        "domain:best2pay.net",
        "domain:vk.com",
        "domain:vk.me",
        "domain:userapi.com",
        "domain:my.com",
        "domain:mycdn.me",
        "domain:vkuseraudio.net",
        "domain:vkuserlive.net",
        "domain:yandex.com",
        "domain:yandex.net",
        "domain:yandexcloud.net",
        "domain:yastatic.net",
        "domain:ozon.com",
        "domain:ozonusercontent.com",
        "domain:avito.st",
        "domain:rambler.co",
        "domain:rutube.com",
        "domain:2gis.com",
        "domain:kaspersky.com",
        "domain:drweb.com",
    ],
    "DirectIp": ["geoip:private", "geoip:ru"],
    "ProxySites": [],
    "ProxyIp": [],
    # Keep the Happ profile independent from optional geosite sections.
    # Category enforcement remains global on the VPN servers.
    "BlockSites": [
        "domain:doubleclick.net",
        "domain:googleadservices.com",
        "domain:googlesyndication.com",
        "domain:googletagservices.com",
        "domain:google-analytics.com",
        "domain:adservice.google.com",
        "domain:ads.youtube.com",
    ],
    "BlockIp": [],
    "DomainStrategy": "IPIfNonMatch",
    "FakeDNS": "false",
}


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
    routing_json = json.dumps(HAPP_ROUTING_PROFILE, ensure_ascii=False, separators=(",", ":"))
    encoded_routing = base64.b64encode(routing_json.encode("utf-8")).decode("ascii")
    filename = f"{SUBSCRIPTION_DISPLAY_NAME}.txt"
    return {
        "profile-title": f"base64:{encoded_title}",
        "Routing-Enable": "true",
        # `onadd` is required for existing Happ installations: `add` updates
        # the profile but only activates it when it is the first routing
        # profile on the device.
        "Routing": f"happ://routing/onadd/{encoded_routing}",
        "profile-update-interval": "24",
        "ping-type": "proxy-head",
        "check-url-via-proxy": "https://cp.cloudflare.com/generate_204",
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
