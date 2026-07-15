"""Бизнес-логика: календарные сдвиги дат и «ленивое истечение»."""
from datetime import date

from dateutil.relativedelta import relativedelta
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import Obligation, Recurrence, Status, utcnow

_RECURRENCE_DELTA = {
    Recurrence.monthly: relativedelta(months=1),
    Recurrence.quarterly: relativedelta(months=3),
    Recurrence.yearly: relativedelta(years=1),
}

DUPLICATE_WARNING = "Активное обязательство с таким названием уже существует"


def shift_next_payment(current: date, recurrence: Recurrence) -> date:
    """Сдвигает дату следующего платежа на один период.

    Сдвиг считается от текущего next_payment_date (а не от даты оплаты),
    чтобы при просрочке не накапливалось смещение.

    relativedelta корректно обрабатывает границы месяцев:
    31.01 + 1 месяц = 28.02 (29.02 в високосный год), а не ошибка.
    """
    return current + _RECURRENCE_DELTA[recurrence]


def apply_lazy_expiry(db: Session, today: date | None = None) -> int:
    """Переводит просроченные РАЗОВЫЕ обязательства (recurrence IS NULL)
    из active в expired.

    Рекуррентные подписки под правило не попадают: просроченная дата
    означает лишь, что пользователь не отметил оплату, — сама подписка
    продолжает действовать, поэтому статус остаётся active.
    """
    today = today or date.today()
    result = db.execute(
        update(Obligation)
        .where(
            Obligation.status == Status.active,
            Obligation.recurrence.is_(None),
            Obligation.next_payment_date < today,
        )
        .values(status=Status.expired, updated_at=utcnow())
        .execution_options(synchronize_session="fetch")
    )
    db.commit()
    return result.rowcount or 0
