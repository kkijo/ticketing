from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AdminUser
from app.core.db import get_db
from app.schemas.venue import (
    HallIn,
    HallOut,
    SeatBulkIn,
    SeatBulkOut,
    VenueIn,
    VenueOut,
)
from app.services import admin as admin_svc

router = APIRouter(prefix="/admin", tags=["admin:venues"])


@router.post("/venues", response_model=VenueOut, status_code=status.HTTP_201_CREATED)
async def create_venue(
    body: VenueIn,
    _user: AdminUser,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> VenueOut:
    venue = await admin_svc.create_venue(
        session, name=body.name, city=body.city, timezone=body.timezone
    )
    return VenueOut.model_validate(venue)


@router.post(
    "/venues/{venue_id}/halls",
    response_model=HallOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_hall(
    venue_id: UUID,
    body: HallIn,
    _user: AdminUser,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> HallOut:
    try:
        hall = await admin_svc.create_hall(
            session, venue_id=venue_id, name=body.name
        )
    except admin_svc.VenueNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Venue not found"
        ) from None
    return HallOut.model_validate(hall)


@router.post(
    "/halls/{hall_id}/seats:bulk",
    response_model=SeatBulkOut,
    status_code=status.HTTP_201_CREATED,
)
async def bulk_create_seats(
    hall_id: UUID,
    body: SeatBulkIn,
    _user: AdminUser,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> SeatBulkOut:
    try:
        count = await admin_svc.bulk_create_seats(
            session,
            hall_id=hall_id,
            rows=body.rows,
            seats_per_row=body.seats_per_row,
            categories=body.categories,
            default_category=body.default_category,
        )
    except admin_svc.HallNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Hall not found"
        ) from None
    return SeatBulkOut(created=count)