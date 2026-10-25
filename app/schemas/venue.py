from uuid import UUID

from pydantic import BaseModel, Field


class VenueIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    city: str = Field(min_length=1, max_length=100)
    timezone: str = "Europe/Moscow"


class VenueOut(BaseModel):
    id: UUID
    name: str
    city: str
    timezone: str

    model_config = {"from_attributes": True}


class HallIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class HallOut(BaseModel):
    id: UUID
    venue_id: UUID
    name: str

    model_config = {"from_attributes": True}


class SeatBulkIn(BaseModel):
    rows: list[str] = Field(min_length=1)
    seats_per_row: int = Field(ge=1, le=100)
    categories: dict[str, str] = Field(default_factory=dict)
    default_category: str = "standard"


class SeatBulkOut(BaseModel):
    created: int