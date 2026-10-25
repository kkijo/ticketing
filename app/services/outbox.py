from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.outbox import Outbox


def emit(session: AsyncSession, topic: str, payload: dict[str, Any]) -> None:
    session.add(Outbox(topic=topic, payload=payload))