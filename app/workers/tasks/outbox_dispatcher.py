import structlog
from sqlalchemy import func, select, update

from app.models.outbox import Outbox

log = structlog.get_logger()


async def dispatch_outbox(ctx: dict) -> int:
    async with ctx["session"]() as session:
        async with session.begin():
            rows = (
                await session.execute(
                    select(Outbox)
                    .where(Outbox.published_at.is_(None))
                    .order_by(Outbox.id)
                    .limit(200)
                    .with_for_update(skip_locked=True)
                )
            ).scalars().all()

            if not rows:
                return 0

            for row in rows:
                log.info("outbox_event", topic=row.topic, payload=row.payload)

            ids = [r.id for r in rows]
            await session.execute(
                update(Outbox)
                .where(Outbox.id.in_(ids))
                .values(published_at=func.now())
                .execution_options(synchronize_session=False)
            )
        return len(rows)