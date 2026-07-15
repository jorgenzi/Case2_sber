"""Pydantic-схемы запросов и ответов."""
import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from app.models import Category, Recurrence, Status


class ObligationCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255, examples=["Яндекс.Плюс"])
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2, examples=[399.00])
    currency: str = Field(examples=["RUB"])
    category: Category
    recurrence: Recurrence | None = Field(
        default=None, description="null — разовое обязательство"
    )
    next_payment_date: date

    @field_validator("title")
    @classmethod
    def _strip_title(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("title не может быть пустым")
        return v

    @field_validator("currency")
    @classmethod
    def _validate_currency(cls, v: str) -> str:
        v = v.strip().upper()
        if len(v) != 3 or not v.isalpha():
            raise ValueError("currency должен быть 3-буквенным кодом ISO 4217 (RUB, USD, EUR)")
        return v


class ObligationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    amount: Decimal
    currency: str
    category: Category
    recurrence: Recurrence | None
    next_payment_date: date
    status: Status
    created_at: datetime
    updated_at: datetime

    @field_serializer("amount")
    def _serialize_amount(self, v: Decimal) -> float:
        return float(v)


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    obligation_id: uuid.UUID
    amount: Decimal
    currency: str
    paid_at: datetime

    @field_serializer("amount")
    def _serialize_amount(self, v: Decimal) -> float:
        return float(v)


class CreateObligationResponse(BaseModel):
    obligation: ObligationOut
    warning: str | None = Field(
        default=None,
        description="Заполняется, если уже существует активное обязательство с таким же названием",
    )


class PayResponse(BaseModel):
    obligation: ObligationOut
    payment: PaymentOut


class RenewalAlert(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    next_payment_date: date
    amount: Decimal
    currency: str

    @field_serializer("amount")
    def _serialize_amount(self, v: Decimal) -> float:
        return float(v)


class UpcomingResponse(BaseModel):
    obligations: list[ObligationOut]
    totals: dict[str, float] = Field(
        description="Суммы по валютам для всех обязательств в окне (без конвертации)"
    )
    renewal_alerts: list[RenewalAlert] = Field(
        description="Только подписки (category=subscription) с recurrence != null"
    )
