import json
from typing import Any

from redis.asyncio import Redis

from app.config import get_settings

settings = get_settings()
redis = Redis.from_url(settings.redis_url, decode_responses=True)


async def get_json(key: str) -> dict[str, Any] | None:
    value = await redis.get(key)
    if not value:
        return None
    return json.loads(value)


async def set_json(key: str, value: dict[str, Any], *, ttl_seconds: int) -> None:
    await redis.set(key, json.dumps(value), ex=ttl_seconds)


async def ping() -> bool:
    return bool(await redis.ping())
