from sqlalchemy import func, select, update

from app.models.event import ShowtimeSeat
from app.models.order import Order, SeatHold
from app.services import outbox as outbox_svc


async def expire_holds(ctx: dict) -> int:
    async with ctx["session"]() as session:
        async with session.begin():
            rows = (
                await session.execute(
                    select(SeatHold.id, SeatHold.order_id)
                    .where(
                        SeatHold.status == "active",
                        SeatHold.expires_at < func.now(),
                    )
                    .order_by(SeatHold.expires_at)
                    .limit(500)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            if not rows:
                return 0

            hold_ids = [r.id for r in rows]
            order_ids = sorted({r.order_id for r in rows}, key=str)

            # Capture affected showtime_ids before we null out order_id
            showtime_ids = list(
                (
                    await session.execute(
                        select(ShowtimeSeat.showtime_id)
                        .where(ShowtimeSeat.hold_id.in_(hold_ids))
                        .distinct()
                    )
                )
                .scalars()
                .all()
            )

            await session.execute(
                update(ShowtimeSeat)
                .where(ShowtimeSeat.hold_id.in_(hold_ids))
                .values(
                    status="available",
                    hold_id=None,
                    hold_expires_at=None,
                    order_id=None,
                )
                .execution_options(synchronize_session=False)
            )
            await session.execute(
                update(SeatHold)
                .where(SeatHold.id.in_(hold_ids))
                .values(status="expired")
                .execution_options(synchronize_session=False)
            )
            await session.execute(
                update(Order)
                .where(Order.id.in_(order_ids), Order.status == "pending")
                .values(status="expired")
                .execution_options(synchronize_session=False)
            )
            for oid in order_ids:
                outbox_svc.emit(session, "order.expired", {"order_id": str(oid)})
        # outside the DB transaction: invalidate caches if redis is available
        redis = ctx.get("redis")
        if redis is not None:
            for sid in showtime_ids:
                await redis.delete(f"showtime:seats:{sid}:v1")
        return len(hold_ids)