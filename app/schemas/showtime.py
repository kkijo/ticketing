from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class SeatOnMap(BaseModel):
    id: UUID
    row: str
    number: int
    category: str
    price_cents: int
    status: str
    x: int | None
    y: int | None


class ShowtimeSeatsOut(BaseModel):
    showtime_id: UUID
    currency: str
    updated_at: datetime
    seats: list[SeatOnMap]