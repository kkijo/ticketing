from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class EventIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str | None = None
    venue_id: UUID
    poster_url: str | None = None


class EventOut(BaseModel):
    id: UUID
    title: str
    description: str | None
    venue_id: UUID
    poster_url: str | None
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class PriceIn(BaseModel):
    category: str
    price_cents: int = Field(ge=0)


class ShowtimeIn(BaseModel):
    hall_id: UUID
    starts_at: datetime
    sales_open_at: datetime | None = None
    sales_close_at: datetime | None = None
    currency: str = "RUB"
    prices: list[PriceIn] = Field(min_length=1)


class ShowtimeOut(BaseModel):
    id: UUID
    event_id: UUID
    hall_id: UUID
    starts_at: datetime
    sales_open_at: datetime | None
    sales_close_at: datetime | None
    status: str
    currency: str

    model_config = {"from_attributes": True}


class ShowtimeWithSeatsOut(ShowtimeOut):
    available_seats: int
    total_seats: int