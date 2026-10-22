"""One async Redis client per event loop.

Celery tasks run each coroutine with asyncio.run(), i.e. on a fresh loop,
and redis-py's asyncio connections cannot be shared across loops.
"""

import asyncio
import weakref

import redis.asyncio as aioredis

from app.config import settings

_clients: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, aioredis.Redis]" = weakref.WeakKeyDictionary()


def get_redis() -> aioredis.Redis:
    loop = asyncio.get_running_loop()
    client = _clients.get(loop)
    if client is None:
        client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        _clients[loop] = client
    return client


async def close_loop_redis() -> None:
    client = _clients.pop(asyncio.get_running_loop(), None)
    if client is not None:
        await client.aclose()
