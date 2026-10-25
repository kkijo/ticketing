from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class PayOrderOut(BaseModel):
    payment_intent_id: str
    client_secret: str
    amount_cents: int
    currency: str


class TicketOut(BaseModel):
    id: UUID
    code: str
    status: str
    qr_payload: str
    issued_at: datetime
    used_at: datetime | None
    seat: dict
    showtime: dict


class CheckInIn(BaseModel):
    qr_payload: str


class CheckInOut(BaseModel):
    ticket_id: UUID
    seat: str
    checked_in_at: datetime
    first_entry: bool