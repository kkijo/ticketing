from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, func, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.event import Showtime, ShowtimeSeat
from app.models.order import Order, OrderItem, SeatHold
from app.models.venue import HallSeat
from app.services import outbox as outbox_svc


class SeatsUnavailable(Exception):
    def __init__(self, conflicts: list[UUID]):
        self.conflicts = conflicts
        super().__init__(f"Unavailable seats: {conflicts}")


class ShowtimeNotFound(Exception):
    pass


class ShowtimeNotOnSale(Exception):
    pass


class InvalidOrderRequest(Exception):
    pass


class OrderNotFound(Exception):
    pass


class OrderForbidden(Exception):
    pass


class CannotCancel(Exception):
    pass


async def _pick_available(
    session: AsyncSession,
    *,
    showtime_id: UUID,
    category: str,
    quantity: int,
) -> list[UUID]:
    stmt = (
        select(ShowtimeSeat.id)
        .join(HallSeat, ShowtimeSeat.hall_seat_id == HallSeat.id)
        .where(
            ShowtimeSeat.showtime_id == showtime_id,
            ShowtimeSeat.status == "available",
            HallSeat.category == category,
        )
        .order_by(HallSeat.row_label, HallSeat.seat_number)
        .limit(quantity)
        .with_for_update(of=ShowtimeSeat, skip_locked=True)
    )
    ids = (await session.execute(stmt)).scalars().all()
    if len(ids) < quantity:
        raise SeatsUnavailable(conflicts=list(ids))
    return list(ids)


async def create_order(
    session: AsyncSession,
    *,
    user_id: UUID,
    showtime_id: UUID,
    seat_ids: list[UUID] | None,
    category: str | None,
    quantity: int | None,
    idempotency_key: str,
    user_agent: str | None,
    ip: str | None,
) -> Order:
    showtime = await session.get(Showtime, showtime_id)
    if showtime is None:
        raise ShowtimeNotFound
    if showtime.status != "on_sale":
        raise ShowtimeNotOnSale

    if seat_ids is not None:
        requested = sorted(seat_ids, key=str)
    elif category is not None and quantity is not None:
        requested = sorted(
            await _pick_available(
                session,
                showtime_id=showtime_id,
                category=category,
                quantity=quantity,
            ),
            key=str,
        )
    else:
        raise InvalidOrderRequest

    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=settings.HOLD_TTL_SEC)

    order = Order(
        user_id=user_id,
        showtime_id=showtime_id,
        status="pending",
        total_cents=0,
        currency=showtime.currency,
        idempotency_key=idempotency_key,
        expires_at=expires_at,
    )
    session.add(order)
    await session.flush()

    hold = SeatHold(order_id=order.id, status="active", expires_at=expires_at)
    session.add(hold)
    await session.flush()

    upd = (
        update(ShowtimeSeat)
        .where(
            ShowtimeSeat.showtime_id == showtime_id,
            ShowtimeSeat.id.in_(requested),
            or_(
                ShowtimeSeat.status == "available",
                and_(
                    ShowtimeSeat.status == "held",
                    ShowtimeSeat.hold_expires_at < func.now(),
                ),
            ),
        )
        .values(
            status="held",
            hold_id=hold.id,
            hold_expires_at=expires_at,
            order_id=order.id,
        )
        .returning(ShowtimeSeat.id, ShowtimeSeat.price_cents)
        .execution_options(synchronize_session=False)
    )
    acquired = (await session.execute(upd)).all()
    if len(acquired) != len(requested):
        acquired_ids = {r.id for r in acquired}
        conflicts = [sid for sid in requested if sid not in acquired_ids]
        raise SeatsUnavailable(conflicts=conflicts)

    total = 0
    items: list[dict] = []
    for row in acquired:
        total += row.price_cents
        items.append(
            {
                "order_id": order.id,
                "showtime_seat_id": row.id,
                "price_cents": row.price_cents,
            }
        )
    await session.execute(insert(OrderItem), items)

    order.total_cents = total
    await session.flush()

    outbox_svc.emit(
        session,
        "order.created",
        {
            "order_id": str(order.id),
            "user_id": str(user_id),
            "showtime_id": str(showtime_id),
            "total_cents": total,
            "currency": showtime.currency,
            "seat_ids": [str(r.id) for r in acquired],
            "expires_at": expires_at.isoformat(),
            "user_agent": user_agent,
            "ip": ip,
        },
    )

    return order


async def cancel_order(
    session: AsyncSession,
    *,
    order_id: UUID,
    actor_id: UUID,
    actor_role: str,
) -> Order:
    order = (
        await session.execute(select(Order).where(Order.id == order_id).with_for_update())
    ).scalar_one_or_none()
    if order is None:
        raise OrderNotFound
    if order.user_id != actor_id and actor_role != "admin":
        raise OrderForbidden
    if order.status != "pending":
        raise CannotCancel

    await session.execute(
        update(ShowtimeSeat)
        .where(ShowtimeSeat.order_id == order.id)
        .values(status="available", hold_id=None, hold_expires_at=None, order_id=None)
        .execution_options(synchronize_session=False)
    )
    await session.execute(
        update(SeatHold)
        .where(SeatHold.order_id == order.id)
        .values(status="released")
        .execution_options(synchronize_session=False)
    )
    order.status = "cancelled"

    outbox_svc.emit(
        session,
        "order.cancelled",
        {"order_id": str(order.id), "user_id": str(order.user_id)},
    )
    await session.flush()
    return order


async def get_order_with_seats(
    session: AsyncSession, order_id: UUID
) -> tuple[Order, list[dict]] | None:
    order = await session.get(Order, order_id)
    if order is None:
        return None
    rows = (
        await session.execute(
            select(
                HallSeat.row_label,
                HallSeat.seat_number,
                HallSeat.category,
                OrderItem.price_cents,
                ShowtimeSeat.id,
            )
            .join(ShowtimeSeat, OrderItem.showtime_seat_id == ShowtimeSeat.id)
            .join(HallSeat, ShowtimeSeat.hall_seat_id == HallSeat.id)
            .where(OrderItem.order_id == order_id)
            .order_by(HallSeat.row_label, HallSeat.seat_number)
        )
    ).all()
    seats = [
        {
            "id": str(r.id),
            "row": r.row_label,
            "number": r.seat_number,
            "category": r.category,
            "price_cents": r.price_cents,
        }
        for r in rows
    ]
    return order, seats