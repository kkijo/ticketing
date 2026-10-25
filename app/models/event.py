from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','published','archived')", name="status_valid"
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    venue_id: Mapped[UUID] = mapped_column(ForeignKey("venues.id"), nullable=False)
    poster_url: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="published")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Showtime(Base):
    __tablename__ = "showtimes"
    __table_args__ = (
        CheckConstraint(
            "status IN ('scheduled','on_sale','closed','cancelled')", name="status_valid"
        ),
        Index("ix_showtimes_event_starts", "event_id", "starts_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    hall_id: Mapped[UUID] = mapped_column(ForeignKey("halls.id"), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sales_open_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sales_close_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="scheduled")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="RUB")


class PriceCategory(Base):
    __tablename__ = "price_categories"
    __table_args__ = (CheckConstraint("price_cents >= 0", name="price_non_negative"),)

    showtime_id: Mapped[UUID] = mapped_column(
        ForeignKey("showtimes.id", ondelete="CASCADE"), primary_key=True
    )
    category: Mapped[str] = mapped_column(String, primary_key=True)
    price_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ShowtimeSeat(Base):
    __tablename__ = "showtime_seats"
    __table_args__ = (
        UniqueConstraint("showtime_id", "hall_seat_id", name="uq_showtime_seat"),
        CheckConstraint(
            "status IN ('available','held','sold')", name="status_valid"
        ),
        Index("ix_showtime_seats_showtime_status", "showtime_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    showtime_id: Mapped[UUID] = mapped_column(
        ForeignKey("showtimes.id", ondelete="CASCADE"), nullable=False
    )
    hall_seat_id: Mapped[UUID] = mapped_column(ForeignKey("hall_seats.id"), nullable=False)
    price_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="available")
    hold_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("seat_holds.id"), nullable=True
    )
    hold_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    order_id: Mapped[UUID | None] = mapped_column(ForeignKey("orders.id"), nullable=True)