from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_cache
from app.core.db import get_db
from app.models.idempotency import IdempotencyKey
from app.models.order import Order
from app.schemas.order import (
    IdempotencyConflict,
    OrderCreateIn,
    OrderOut,
    RequestInFlight,
)
from app.services import idempotency as idem_svc
from app.services import orders as orders_svc
from app.services import payments as payments_svc
from app.services import showtimes as showtimes_svc

router = APIRouter(prefix="/orders", tags=["orders"])


def _to_out(order, seats: list[dict]) -> dict:
    return {
        "id": str(order.id),
        "status": order.status,
        "total_cents": order.total_cents,
        "currency": order.currency,
        "showtime_id": str(order.showtime_id),
        "expires_at": order.expires_at.isoformat() if order.expires_at else None,
        "created_at": order.created_at.isoformat(),
        "seats": seats,
    }


@router.post("", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
async def create_order(
    body: OrderCreateIn,
    request: Request,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Redis, Depends(get_cache)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict:
    user_id = user.id

    if not idempotency_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Idempotency-Key header is required",
        )

    fp = idem_svc.request_fingerprint(body.model_dump(mode="json"))

    # Fast path: replay from DB. Capture values before rollback expires the row.
    existing = (
        await session.execute(
            select(IdempotencyKey).where(
                IdempotencyKey.user_id == user_id,
                IdempotencyKey.key == idempotency_key,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        stored_hash = existing.request_hash
        response_body = existing.response_body
        await session.rollback()
        if stored_hash != fp:
            raise HTTPException(
                status_code=422,
                detail="Idempotency-Key reused with different request body",
            )
        if response_body is None:
            raise HTTPException(
                status_code=500,
                detail="Idempotency record corrupted",
            )
        return response_body

    await session.rollback()

    try:
        await idem_svc.acquire(cache, user_id, idempotency_key, fp)
    except IdempotencyConflict:
        raise HTTPException(
            status_code=422,
            detail="Idempotency-Key reused with different request body",
        ) from None
    except RequestInFlight:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Request in flight, retry shortly",
        ) from None

    try:
        try:
            order = await orders_svc.create_order(
                session,
                user_id=user_id,
                showtime_id=body.showtime_id,
                seat_ids=body.seat_ids,
                category=body.category,
                quantity=body.quantity,
                idempotency_key=idempotency_key,
                user_agent=request.headers.get("user-agent"),
                ip=request.client.host if request.client else None,
            )
        except orders_svc.ShowtimeNotFound:
            await session.rollback()
            raise HTTPException(
                status_code=404, detail="Showtime not found"
            ) from None
        except orders_svc.ShowtimeNotOnSale:
            await session.rollback()
            raise HTTPException(
                status_code=409, detail="Showtime is not on sale"
            ) from None
        except orders_svc.InvalidOrderRequest:
            await session.rollback()
            raise HTTPException(
                status_code=422,
                detail="Provide either seat_ids or category+quantity",
            ) from None
        except orders_svc.SeatsUnavailable as e:
            await session.rollback()
            raise HTTPException(
                status_code=409,
                detail={
                    "type": "seat-unavailable",
                    "title": "Seat unavailable",
                    "conflicts": [str(c) for c in e.conflicts],
                },
            ) from None

        order_data = await orders_svc.get_order_with_seats(session, order.id)
        if order_data is None:
            raise HTTPException(
                status_code=500, detail="Order vanished after creation"
            )
        _, seats = order_data
        payload = _to_out(order, seats)
        session.add(
            IdempotencyKey(
                user_id=user_id,
                key=idempotency_key,
                request_hash=fp,
                response_status=201,
                response_body=payload,
            )
        )
        await session.commit()

        # Invalidate the seat map so the next GET /seats reflects the new 'held'
        await showtimes_svc.invalidate_cache(cache, body.showtime_id)

        return payload
    except IntegrityError:
        await session.rollback()
        existing = (
            await session.execute(
                select(IdempotencyKey).where(
                    IdempotencyKey.user_id == user_id,
                    IdempotencyKey.key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None and existing.request_hash == fp:
            body_cached = existing.response_body
            if body_cached is None:
                raise HTTPException(
                    status_code=500,
                    detail="Idempotency record corrupted",
                ) from None
            return body_cached
        raise HTTPException(
            status_code=409, detail="Idempotency conflict"
        ) from None
    finally:
        await idem_svc.release(cache, user_id, idempotency_key)


@router.get("/{order_id}", response_model=OrderOut)
async def get_order(
    order_id: UUID,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    user_id = user.id
    user_role = user.role

    result = await orders_svc.get_order_with_seats(session, order_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Order not found")
    order, seats = result
    if order.user_id != user_id and user_role != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    return _to_out(order, seats)


@router.post("/{order_id}/cancel", response_model=OrderOut)
async def cancel_order(
    order_id: UUID,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[Redis, Depends(get_cache)],
) -> dict:
    user_id = user.id
    user_role = user.role

    try:
        order = await orders_svc.cancel_order(
            session, order_id=order_id, actor_id=user_id, actor_role=user_role
        )
        order_data = await orders_svc.get_order_with_seats(session, order.id)
        if order_data is None:
            raise HTTPException(
                status_code=500, detail="Order vanished after cancellation"
            )
        _, seats = order_data
        payload = _to_out(order, seats)
        showtime_id = order.showtime_id
        await session.commit()
        await showtimes_svc.invalidate_cache(cache, showtime_id)
        return payload
    except orders_svc.OrderNotFound:
        await session.rollback()
        raise HTTPException(
            status_code=404, detail="Order not found"
        ) from None
    except orders_svc.OrderForbidden:
        await session.rollback()
        raise HTTPException(status_code=403, detail="Forbidden") from None
    except orders_svc.CannotCancel:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="Order cannot be cancelled"
        ) from None


@router.post("/{order_id}/pay", response_model=None)
async def pay_order(
    order_id: UUID,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    user_id = user.id

    order = (
        await session.execute(
            select(Order).where(Order.id == order_id, Order.user_id == user_id)
        )
    ).scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status != "pending":
        raise HTTPException(status_code=409, detail="Order is not payable")
    if order.expires_at is not None and order.expires_at < datetime.now(UTC):
        raise HTTPException(status_code=409, detail="Hold expired")

    # Capture every value we need before rollback() expires the ORM object.
    captured_id = order.id
    captured_amount = order.total_cents
    captured_currency = order.currency

    await session.rollback()

    try:
        pi = await payments_svc.create_payment_intent(
            order_id=captured_id,
            amount_cents=captured_amount,
            currency=captured_currency,
        )
    except payments_svc.PaymentProviderError:
        raise HTTPException(
            status_code=502, detail="Payment provider unavailable"
        ) from None

    return {
        "payment_intent_id": pi["payment_intent_id"],
        "client_secret": pi["client_secret"],
        "amount_cents": captured_amount,
        "currency": captured_currency,
    }