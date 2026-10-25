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


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','paid','expired','cancelled','refunded')",
            name="status_valid",
        ),
        UniqueConstraint("user_id", "idempotency_key", name="uq_order_user_idem"),
        Index(
            "ix_orders_pending_expires",
            "status",
            "expires_at",
            postgresql_where=text("status = 'pending'"),
        ),
        Index("ix_orders_user_created", "user_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    showtime_id: Mapped[UUID] = mapped_column(ForeignKey("showtimes.id"), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="pending")
    total_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OrderItem(Base):
    __tablename__ = "order_items"

    order_id: Mapped[UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), primary_key=True
    )
    showtime_seat_id: Mapped[UUID] = mapped_column(
        ForeignKey("showtime_seats.id"), primary_key=True
    )
    price_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)


class SeatHold(Base):
    __tablename__ = "seat_holds"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','converted','expired','released')", name="status_valid"
        ),
        Index(
            "ix_seat_holds_active_expires",
            "expires_at",
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    order_id: Mapped[UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="active")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )