from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_cache
from app.core.db import get_db
from app.schemas.event import ShowtimeWithSeatsOut
from app.schemas.showtime import ShowtimeSeatsOut
from app.services import admin as admin_svc
from app.services import showtimes as showtimes_svc

router = APIRouter(prefix="/showtimes", tags=["showtimes"])


@router.get("/{showtime_id}", response_model=ShowtimeWithSeatsOut)
async def get_showtime(
    showtime_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> ShowtimeWithSeatsOut:
    result = await admin_svc.get_showtime_with_counts(session, showtime_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Showtime not found"
        )
    showtime, available, total = result
    return ShowtimeWithSeatsOut(
        id=showtime.id,
        event_id=showtime.event_id,
        hall_id=showtime.hall_id,
        starts_at=showtime.starts_at,
        sales_open_at=showtime.sales_open_at,
        sales_close_at=showtime.sales_close_at,
        status=showtime.status,
        currency=showtime.currency,
        available_seats=available,
        total_seats=total,
    )


@router.get("/{showtime_id}/seats", response_model=ShowtimeSeatsOut)
async def get_seats(
    showtime_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Redis, Depends(get_cache)],
) -> ShowtimeSeatsOut:
    payload = await showtimes_svc.get_seats_map(session, cache, showtime_id)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Showtime not found"
        )
    return ShowtimeSeatsOut.model_validate(payload)