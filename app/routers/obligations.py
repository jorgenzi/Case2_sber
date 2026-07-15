import uuid
from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Category, Obligation, Payment, Status, utcnow
from app.schemas import (
    CreateObligationResponse,
    ObligationCreate,
    ObligationOut,
    PayResponse,
    UpcomingResponse,
)
from app.services import DUPLICATE_WARNING, apply_lazy_expiry, shift_next_payment
from app.sse import broker

router = APIRouter(prefix="/obligations", tags=["obligations"])


def _get_obligation_or_404(db: Session, obligation_id: uuid.UUID) -> Obligation:
    obligation = db.get(Obligation, obligation_id)
    if obligation is None:
        raise HTTPException(status_code=404, detail="Обязательство не найдено")
    return obligation


@router.post(
    "",
    status_code=201,
    response_model=CreateObligationResponse,
    summary="Создать обязательство",
)
def create_obligation(payload: ObligationCreate, db: Session = Depends(get_db)):
    """Создаёт обязательство.

    * Дата в прошлом — не ошибка: запись сразу получает статус `expired`
      (AI-модуль может добавить обязательство постфактум по старому чеку).
    * Дубль по названию — не ошибка: запись создаётся, но в ответ
      добавляется `warning` (AI-модуль может повторно распарсить письмо).
    """
    status = (
        Status.expired
        if payload.next_payment_date < date.today()
        else Status.active
    )

    duplicate_exists = db.execute(
        select(Obligation.id)
        .where(
            Obligation.status == Status.active,
            func.lower(Obligation.title) == payload.title.lower(),
        )
        .limit(1)
    ).first()

    obligation = Obligation(**payload.model_dump(), status=status)
    db.add(obligation)
    db.commit()
    db.refresh(obligation)

    return {
        "obligation": obligation,
        "warning": DUPLICATE_WARNING if duplicate_exists else None,
    }


@router.get(
    "",
    response_model=list[ObligationOut],
    summary="Список обязательств",
)
def list_obligations(
    category: Category | None = Query(default=None),
    status: Status | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Перед формированием ответа применяется lazy expiry: просроченные
    разовые обязательства переводятся в `expired`; рекуррентные остаются
    `active` (обоснование — в README). Фильтры `category` и `status`
    можно комбинировать. Сортировка — по `next_payment_date` по возрастанию.
    """
    apply_lazy_expiry(db)

    query = select(Obligation)
    if category is not None:
        query = query.where(Obligation.category == category)
    if status is not None:
        query = query.where(Obligation.status == status)
    query = query.order_by(Obligation.next_payment_date.asc())

    return db.scalars(query).all()


@router.get(
    "/upcoming",
    response_model=UpcomingResponse,
    summary="Обязательства на ближайшие N дней",
)
def upcoming_obligations(
    days: int = Query(default=7, ge=0, le=3650),
    db: Session = Depends(get_db),
):
    """Активные обязательства с `next_payment_date` в окне
    `[today, today + days]`.

    * `totals` — суммы по валютам (без конвертации на бэкенде);
    * `renewal_alerts` — только подписки (`category=subscription`,
      `recurrence != null`): скоро спишут деньги, ещё можно отменить.
    """
    apply_lazy_expiry(db)

    today = date.today()
    window_end = today + timedelta(days=days)

    obligations = db.scalars(
        select(Obligation)
        .where(
            Obligation.status == Status.active,
            Obligation.next_payment_date >= today,
            Obligation.next_payment_date <= window_end,
        )
        .order_by(Obligation.next_payment_date.asc())
    ).all()

    totals: dict[str, Decimal] = {}
    for ob in obligations:
        totals[ob.currency] = totals.get(ob.currency, Decimal("0")) + ob.amount

    renewal_alerts = [
        ob
        for ob in obligations
        if ob.category == Category.subscription and ob.recurrence is not None
    ]

    return {
        "obligations": obligations,
        "totals": {currency: float(total) for currency, total in totals.items()},
        "renewal_alerts": renewal_alerts,
    }


@router.post(
    "/{obligation_id}/pay",
    response_model=PayResponse,
    summary="Зафиксировать оплату",
)
def pay_obligation(obligation_id: uuid.UUID, db: Session = Depends(get_db)):
    """Фиксирует оплату:

    * статус не `active` → 422;
    * создаётся запись в `payments` с текущими amount/currency;
    * рекуррентные: `next_payment_date` сдвигается на период от текущей
      даты платежа (не от даты оплаты — иначе накапливается смещение),
      статус остаётся `active`; граница месяца обрабатывается корректно
      (31.01 + 1 мес = 28/29.02);
    * разовые (`recurrence = null`): статус → `cancelled`.
    """
    obligation = _get_obligation_or_404(db, obligation_id)

    if obligation.status != Status.active:
        raise HTTPException(
            status_code=422,
            detail=(
                "Оплатить можно только обязательство со статусом active; "
                f"текущий статус: {obligation.status.value}"
            ),
        )

    payment = Payment(
        obligation_id=obligation.id,
        amount=obligation.amount,
        currency=obligation.currency,
        paid_at=utcnow(),
    )
    db.add(payment)

    if obligation.recurrence is not None:
        obligation.next_payment_date = shift_next_payment(
            obligation.next_payment_date, obligation.recurrence
        )
        # статус остаётся active
    else:
        obligation.status = Status.cancelled  # разовое обязательство закрыто

    db.commit()
    db.refresh(obligation)
    db.refresh(payment)

    return {"obligation": obligation, "payment": payment}


@router.patch(
    "/{obligation_id}/cancel",
    response_model=ObligationOut,
    summary="Отменить обязательство",
)
def cancel_obligation(obligation_id: uuid.UUID, db: Session = Depends(get_db)):
    """Переводит обязательство в `cancelled`. Запись остаётся в базе.
    Отменить можно только `active`; попытка отменить `expired` или уже
    `cancelled` → 422.
    """
    obligation = _get_obligation_or_404(db, obligation_id)

    if obligation.status != Status.active:
        raise HTTPException(
            status_code=422,
            detail=(
                "Отменить можно только обязательство со статусом active; "
                f"текущий статус: {obligation.status.value}"
            ),
        )

    obligation.status = Status.cancelled
    db.commit()
    db.refresh(obligation)
    return obligation


@router.delete(
    "/{obligation_id}",
    status_code=204,
    summary="Удалить обязательство",
)
def delete_obligation(obligation_id: uuid.UUID, db: Session = Depends(get_db)):
    """Удаляет обязательство вместе с историей платежей (любой статус,
    без дополнительных проверок). После удаления транслирует SSE-событие
    `obligation_deleted` подписчикам `GET /events`.
    """
    obligation = _get_obligation_or_404(db, obligation_id)
    deleted_id = str(obligation.id)

    db.delete(obligation)  # payments удаляются каскадно
    db.commit()

    broker.publish({"type": "obligation_deleted", "id": deleted_id})

    return Response(status_code=204)
