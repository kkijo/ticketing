from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (
        CheckConstraint(
            "status IN ('valid','used','refunded','void')", name="status_valid"
        ),
        Index(
            "uq_tickets_active_seat",
            "showtime_seat_id",
            unique=True,
            postgresql_where=text("status = 'valid'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"), nullable=False)
    showtime_seat_id: Mapped[UUID] = mapped_column(
        ForeignKey("showtime_seats.id"), nullable=False
    )
    code: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="valid")
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    used_by_staff_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )