import json
from datetime import UTC, datetime
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import Showtime, ShowtimeSeat
from app.models.venue import HallSeat

CACHE_TTL = 3


async def get_seats_map(
    session: AsyncSession, cache: Redis, showtime_id: UUID
) -> dict | None:
    showtime = await session.get(Showtime, showtime_id)
    if showtime is None:
        return None

    cache_key = f"showtime:seats:{showtime_id}:v1"
    cached = await cache.get(cache_key)
    if cached is not None:
        return json.loads(cached)

    rows = (
        await session.execute(
            select(
                ShowtimeSeat.id,
                HallSeat.row_label,
                HallSeat.seat_number,
                HallSeat.category,
                ShowtimeSeat.price_cents,
                ShowtimeSeat.status,
                ShowtimeSeat.hold_expires_at,
                HallSeat.x,
                HallSeat.y,
            )
            .join(HallSeat, ShowtimeSeat.hall_seat_id == HallSeat.id)
            .where(ShowtimeSeat.showtime_id == showtime_id)
            .order_by(HallSeat.row_label, HallSeat.seat_number)
        )
    ).all()

    now = datetime.now(UTC)
    seats = []
    for r in rows:
        status = r.status
        if (
            status == "held"
            and r.hold_expires_at is not None
            and r.hold_expires_at < now
        ):
            status = "available"
        seats.append(
            {
                "id": str(r.id),
                "row": r.row_label,
                "number": r.seat_number,
                "category": r.category,
                "price_cents": r.price_cents,
                "status": status,
                "x": r.x,
                "y": r.y,
            }
        )

    payload = {
        "showtime_id": str(showtime_id),
        "currency": showtime.currency,
        "updated_at": now.isoformat(),
        "seats": seats,
    }
    await cache.setex(cache_key, CACHE_TTL, json.dumps(payload))
    return payload


async def invalidate_cache(cache: Redis, showtime_id: UUID) -> None:
    await cache.delete(f"showtime:seats:{showtime_id}:v1")