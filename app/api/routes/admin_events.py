from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AdminUser
from app.core.db import get_db
from app.schemas.event import EventIn, EventOut, ShowtimeIn, ShowtimeOut
from app.services import admin as admin_svc

router = APIRouter(prefix="/admin", tags=["admin:events"])


@router.post("/events", response_model=EventOut, status_code=status.HTTP_201_CREATED)
async def create_event(
    body: EventIn,
    _user: AdminUser,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> EventOut:
    try:
        event = await admin_svc.create_event(
            session,
            title=body.title,
            description=body.description,
            venue_id=body.venue_id,
            poster_url=body.poster_url,
        )
    except admin_svc.VenueNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Venue not found"
        ) from None
    return EventOut.model_validate(event)


@router.post(
    "/events/{event_id}/showtimes",
    response_model=ShowtimeOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_showtime(
    event_id: UUID,
    body: ShowtimeIn,
    _user: AdminUser,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> ShowtimeOut:
    prices = {p.category: p.price_cents for p in body.prices}
    try:
        showtime = await admin_svc.create_showtime(
            session,
            event_id=event_id,
            hall_id=body.hall_id,
            starts_at=body.starts_at,
            sales_open_at=body.sales_open_at,
            sales_close_at=body.sales_close_at,
            currency=body.currency,
            prices=prices,
        )
    except admin_svc.HallNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Hall not found"
        ) from None
    except admin_svc.EventNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Event not found"
        ) from None
    except admin_svc.MissingPriceCategories as e:
        raise HTTPException(
            status_code=422,
            detail={"missing_categories": e.categories},
        ) from None
    return ShowtimeOut.model_validate(showtime)