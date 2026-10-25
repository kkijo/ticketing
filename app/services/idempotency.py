import hashlib
import json

from redis.asyncio import Redis


def request_fingerprint(body: dict) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def redis_key(user_id, key: str) -> str:
    return f"idem:{user_id}:{key}"


async def acquire(redis: Redis, user_id, key: str, fp: str, *, ttl: int = 30) -> None:
    """Raises RequestInFlight or IdempotencyConflict."""
    from app.schemas.order import IdempotencyConflict, RequestInFlight

    rkey = redis_key(user_id, key)
    ok = await redis.set(rkey, fp, nx=True, ex=ttl)
    if not ok:
        stored = await redis.get(rkey)
        if stored != fp:
            raise IdempotencyConflict
        raise RequestInFlight


async def release(redis: Redis, user_id, key: str) -> None:
    await redis.delete(redis_key(user_id, key))