import redis.asyncio as redis
from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import settings


async def create_arq_pool() -> ArqRedis:
    return await create_pool(RedisSettings.from_dsn(str(settings.REDIS_URL)))


def create_cache_client() -> redis.Redis:
    return redis.from_url(str(settings.REDIS_URL), decode_responses=True)