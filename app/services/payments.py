import asyncio
from uuid import UUID

import stripe
from sqlalchemy import func, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.event import ShowtimeSeat
from app.models.order import Order, SeatHold
from app.models.payment import Payment, PaymentEvent
from app.services import outbox as outbox_svc

if settings.STRIPE_SECRET_KEY is not None:
    stripe.api_key = settings.STRIPE_SECRET_KEY.get_secret_value()


class PaymentProviderError(Exception):
    pass


async def create_payment_intent(
    *, order_id: UUID, amount_cents: int, currency: str
) -> dict:
    if settings.STRIPE_SECRET_KEY is None:
        raise PaymentProviderError("STRIPE_SECRET_KEY not configured")

    def _create():
        return stripe.PaymentIntent.create(
            amount=amount_cents,
            currency=currency.lower(),
            metadata={"order_id": str(order_id)},
            automatic_payment_methods={"enabled": True},
        )

    try:
        pi = await asyncio.to_thread(_create)
    except stripe.error.StripeError as e:
        raise PaymentProviderError(str(e)) from e

    return {
        "payment_intent_id": pi["id"],
        "client_secret": pi["client_secret"],
        "amount_cents": pi["amount"],
        "currency": pi["currency"].upper(),
    }


async def _refund(payment_intent_id: str) -> None:
    def _do():
        return stripe.Refund.create(payment_intent=payment_intent_id)

    await asyncio.to_thread(_do)


async def handle_payment_succeeded(
    session: AsyncSession, *, event_id: str, intent: dict
) -> str:
    """Returns one of: duplicate, no_order, paid, refunded."""
    ins = await session.execute(
        pg_insert(PaymentEvent)
        .values(id=event_id, provider="stripe", payload=intent)
        .on_conflict_do_nothing(index_elements=["id"])
        .returning(PaymentEvent.id)
    )
    if ins.scalar_one_or_none() is None:
        await session.commit()
        return "duplicate"

    order_id_str = (intent.get("metadata") or {}).get("order_id")
    if not order_id_str:
        await session.commit()
        return "no_order"
    order_id = UUID(order_id_str)
    pi_id = intent["id"]
    amount = int(intent["amount"])

    upd = await session.execute(
        update(Order)
        .where(Order.id == order_id, Order.status == "pending")
        .values(status="paid", paid_at=func.now(), expires_at=None)
        .returning(Order.id)
        .execution_options(synchronize_session=False)
    )
    moved = upd.scalar_one_or_none()

    if moved is None:
        # Order already expired/cancelled — refund money
        try:
            await _refund(pi_id)
        except stripe.error.StripeError:
            # Still record what we know; a reconciler would retry later
            pass
        await session.execute(
            pg_insert(Payment)
            .values(
                order_id=order_id,
                provider="stripe",
                provider_payment_id=pi_id,
                amount_cents=amount,
                status="refunded",
            )
            .on_conflict_do_nothing(
                index_elements=["provider", "provider_payment_id"],
                index_where=Payment.provider_payment_id.isnot(None),
            )
        )
        outbox_svc.emit(
            session,
            "order.payment_after_expiry",
            {"order_id": str(order_id), "payment_intent_id": pi_id},
        )
        await session.commit()
        return "refunded"

    await session.execute(
        update(ShowtimeSeat)
        .where(ShowtimeSeat.order_id == order_id)
        .values(status="sold")
        .execution_options(synchronize_session=False)
    )
    await session.execute(
        update(SeatHold)
        .where(SeatHold.order_id == order_id)
        .values(status="converted")
        .execution_options(synchronize_session=False)
    )

    await session.execute(
        text(
            """
            INSERT INTO tickets (order_id, showtime_seat_id, code, status)
            SELECT oi.order_id, oi.showtime_seat_id,
                   encode(gen_random_bytes(16), 'hex'), 'valid'
            FROM order_items oi
            WHERE oi.order_id = :oid
            """
        ),
        {"oid": order_id},
    )

    await session.execute(
        pg_insert(Payment)
        .values(
            order_id=order_id,
            provider="stripe",
            provider_payment_id=pi_id,
            amount_cents=amount,
            status="succeeded",
        )
        .on_conflict_do_nothing(
            index_elements=["provider", "provider_payment_id"],
            index_where=Payment.provider_payment_id.isnot(None),
        )
    )

    outbox_svc.emit(session, "order.paid", {"order_id": str(order_id), "payment_intent_id": pi_id})

    await session.commit()
    return "paid"