from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class OrderCreateIn(BaseModel):
    showtime_id: UUID
    seat_ids: list[UUID] | None = None
    category: str | None = None
    quantity: int | None = Field(default=None, ge=1, le=20)


class OrderSeatOut(BaseModel):
    id: UUID
    row: str
    number: int
    category: str
    price_cents: int


class OrderOut(BaseModel):
    id: UUID
    status: str
    total_cents: int
    currency: str
    showtime_id: UUID
    expires_at: datetime | None
    created_at: datetime
    seats: list[OrderSeatOut]


class IdempotencyConflict(Exception):
    pass


class RequestInFlight(Exception):
    pass