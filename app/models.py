"""Модели данных: обязательства и история платежей."""
import enum
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Category(str, enum.Enum):
    subscription = "subscription"
    warranty = "warranty"
    bill = "bill"
    insurance = "insurance"


class Recurrence(str, enum.Enum):
    monthly = "monthly"
    quarterly = "quarterly"
    yearly = "yearly"


class Status(str, enum.Enum):
    active = "active"
    cancelled = "cancelled"
    expired = "expired"


class Base(DeclarativeBase):
    pass


class Obligation(Base):
    __tablename__ = "obligations"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    category: Mapped[Category] = mapped_column(
        Enum(Category, name="category", native_enum=False, length=20), nullable=False
    )
    recurrence: Mapped[Recurrence | None] = mapped_column(
        Enum(Recurrence, name="recurrence", native_enum=False, length=20),
        nullable=True,
    )
    next_payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[Status] = mapped_column(
        Enum(Status, name="status", native_enum=False, length=20),
        nullable=False,
        default=Status.active,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    payments: Mapped[list["Payment"]] = relationship(
        back_populates="obligation", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_obligations_status_next_payment_date", "status", "next_payment_date"),
    )


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    obligation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("obligations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    paid_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    obligation: Mapped[Obligation] = relationship(back_populates="payments")
