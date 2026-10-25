from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, StaffUser
from app.core.db import get_db
from app.models.event import Showtime, ShowtimeSeat
from app.models.order import Order
from app.models.ticket import Ticket
from app.models.venue import HallSeat
from app.schemas.ticket import CheckInIn, CheckInOut, TicketOut
from app.services import tickets as tickets_svc

router = APIRouter(tags=["tickets"])


def _ticket_payload(
    ticket: Ticket,
    row_label: str,
    seat_number: int,
    category: str,
    showtime: Showtime,
    event_title: str,
) -> dict:
    return {
        "id": str(ticket.id),
        "code": ticket.code,
        "status": ticket.status,
        "qr_payload": tickets_svc.qr_payload(ticket.code),
        "issued_at": ticket.issued_at.isoformat(),
        "used_at": ticket.used_at.isoformat() if ticket.used_at else None,
        "seat": {
            "row": row_label,
            "number": seat_number,
            "category": category,
        },
        "showtime": {
            "id": str(showtime.id),
            "starts_at": showtime.starts_at.isoformat(),
            "currency": showtime.currency,
            "event_title": event_title,
        },
    }


@router.get("/me/tickets", response_model=list[TicketOut])
async def my_tickets(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict]:
    user_id = user.id
    rows = (
        await session.execute(
            select(Ticket, HallSeat, Showtime)
            .join(Order, Ticket.order_id == Order.id)
            .join(ShowtimeSeat, Ticket.showtime_seat_id == ShowtimeSeat.id)
            .join(HallSeat, ShowtimeSeat.hall_seat_id == HallSeat.id)
            .join(Showtime, ShowtimeSeat.showtime_id == Showtime.id)
            .where(Order.user_id == user_id)
            .order_by(Showtime.starts_at)
        )
    ).all()

    return [
        _ticket_payload(ticket, hs.row_label, hs.seat_number, hs.category, st, "")
        for ticket, hs, st in rows
    ]


@router.get("/tickets/{ticket_id}", response_model=TicketOut)
async def get_ticket(
    ticket_id: UUID,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    user_id = user.id
    user_role = user.role

    row = (
        await session.execute(
            select(Ticket, HallSeat, Showtime, Order)
            .join(Order, Ticket.order_id == Order.id)
            .join(ShowtimeSeat, Ticket.showtime_seat_id == ShowtimeSeat.id)
            .join(HallSeat, ShowtimeSeat.hall_seat_id == HallSeat.id)
            .join(Showtime, ShowtimeSeat.showtime_id == Showtime.id)
            .where(Ticket.id == ticket_id)
        )
    ).first()

    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

    ticket, hs, st, order = row
    if order.user_id != user_id and user_role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    return _ticket_payload(ticket, hs.row_label, hs.seat_number, hs.category, st, "")


@router.post("/check-in", response_model=CheckInOut)
async def check_in(
    body: CheckInIn,
    staff: StaffUser,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    code = tickets_svc.verify_qr_payload(body.qr_payload)
    if code is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid QR signature"
        )

    staff_id = staff.id
    now = datetime.now(UTC)

    upd = await session.execute(
        update(Ticket)
        .where(Ticket.code == code, Ticket.status == "valid")
        .values(status="used", used_at=now, used_by_staff_id=staff_id)
        .returning(Ticket.id, Ticket.showtime_seat_id)
        .execution_options(synchronize_session=False)
    )
    row = upd.one_or_none()

    if row is None:
        existing = (
            await session.execute(select(Ticket).where(Ticket.code == code))
        ).scalar_one_or_none()
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found"
            )
        if existing.status == "used":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "type": "ticket-already-used",
                    "used_at": existing.used_at.isoformat() if existing.used_at else None,
                },
            )
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail=f"Ticket is {existing.status}"
        )

    seat = (
        await session.execute(
            select(HallSeat.row_label, HallSeat.seat_number)
            .join(ShowtimeSeat, ShowtimeSeat.hall_seat_id == HallSeat.id)
            .where(ShowtimeSeat.id == row.showtime_seat_id)
        )
    ).one()

    await session.commit()

    return {
        "ticket_id": row.id,
        "seat": f"{seat.row_label}{seat.seat_number}",
        "checked_in_at": now,
        "first_entry": True,
    }