import calendar
from datetime import date, timedelta

import pytest

from app.models import Recurrence
from app.services import shift_next_payment
from tests.conftest import obligation_payload


# ---------- Unit-тесты чистой функции сдвига дат ----------

@pytest.mark.parametrize(
    "current, recurrence, expected",
    [
        # 31 января + 1 месяц = 28 февраля (обычный год)
        (date(2025, 1, 31), Recurrence.monthly, date(2025, 2, 28)),
        # 31 января + 1 месяц = 29 февраля (високосный год)
        (date(2024, 1, 31), Recurrence.monthly, date(2024, 2, 29)),
        # 31 марта + 1 месяц = 30 апреля
        (date(2025, 3, 31), Recurrence.monthly, date(2025, 4, 30)),
        # обычный сдвиг без границ
        (date(2025, 6, 15), Recurrence.monthly, date(2025, 7, 15)),
        # квартальный: 30 ноября + 3 месяца = 28/29 февраля
        (date(2025, 11, 30), Recurrence.quarterly, date(2026, 2, 28)),
        (date(2025, 5, 10), Recurrence.quarterly, date(2025, 8, 10)),
        # годовой: 29 февраля високосного года -> 28 февраля следующего
        (date(2024, 2, 29), Recurrence.yearly, date(2025, 2, 28)),
        (date(2025, 7, 1), Recurrence.yearly, date(2026, 7, 1)),
    ],
)
def test_shift_next_payment_handles_calendar_edges(current, recurrence, expected):
    assert shift_next_payment(current, recurrence) == expected


# ---------- API-тесты /pay ----------

def _create(client, **overrides) -> dict:
    response = client.post("/obligations", json=obligation_payload(**overrides))
    assert response.status_code == 201
    return response.json()["obligation"]


def test_pay_monthly_shifts_one_month_and_stays_active(client):
    start = date.today() + timedelta(days=5)
    obligation = _create(client, recurrence="monthly",
                         next_payment_date=start.isoformat())

    response = client.post(f"/obligations/{obligation['id']}/pay")
    assert response.status_code == 200
    body = response.json()

    assert body["obligation"]["status"] == "active"
    expected = shift_next_payment(start, Recurrence.monthly)
    assert body["obligation"]["next_payment_date"] == expected.isoformat()


def test_pay_shift_counted_from_next_payment_date_not_payment_date(client):
    """Сдвиг считается от текущего next_payment_date, а не от даты оплаты —
    иначе при ранней/поздней оплате накапливается смещение."""
    start = date.today() + timedelta(days=20)  # платим за 20 дней до срока
    obligation = _create(client, recurrence="monthly",
                         next_payment_date=start.isoformat())

    body = client.post(f"/obligations/{obligation['id']}/pay").json()

    expected = shift_next_payment(start, Recurrence.monthly)
    assert body["obligation"]["next_payment_date"] == expected.isoformat()
    # а не today + 1 месяц
    assert body["obligation"]["next_payment_date"] != (
        shift_next_payment(date.today(), Recurrence.monthly).isoformat()
    )


def test_pay_on_january_31_monthly_moves_to_end_of_february(client):
    """Граничный случай из ТЗ: оплата 31-го числа с recurrence=monthly."""
    year = date.today().year
    jan_31 = date(year, 1, 31)
    if jan_31 <= date.today():
        jan_31 = date(year + 1, 1, 31)

    obligation = _create(client, recurrence="monthly",
                         next_payment_date=jan_31.isoformat())

    body = client.post(f"/obligations/{obligation['id']}/pay").json()

    last_feb_day = 29 if calendar.isleap(jan_31.year) else 28
    assert body["obligation"]["next_payment_date"] == (
        date(jan_31.year, 2, last_feb_day).isoformat()
    )
    assert body["obligation"]["status"] == "active"


def test_pay_quarterly_shifts_three_months(client):
    start = date.today() + timedelta(days=3)
    obligation = _create(client, recurrence="quarterly",
                         next_payment_date=start.isoformat())

    body = client.post(f"/obligations/{obligation['id']}/pay").json()
    expected = shift_next_payment(start, Recurrence.quarterly)
    assert body["obligation"]["next_payment_date"] == expected.isoformat()
    assert body["obligation"]["status"] == "active"


def test_pay_yearly_shifts_one_year(client):
    start = date.today() + timedelta(days=3)
    obligation = _create(client, recurrence="yearly",
                         next_payment_date=start.isoformat())

    body = client.post(f"/obligations/{obligation['id']}/pay").json()
    expected = shift_next_payment(start, Recurrence.yearly)
    assert body["obligation"]["next_payment_date"] == expected.isoformat()
    assert body["obligation"]["status"] == "active"


def test_pay_one_off_closes_obligation_as_cancelled(client):
    obligation = _create(client, recurrence=None)

    body = client.post(f"/obligations/{obligation['id']}/pay").json()
    assert body["obligation"]["status"] == "cancelled"


def test_pay_creates_payment_record_with_current_amount_and_currency(client):
    obligation = _create(client, amount=9.99, currency="USD")

    body = client.post(f"/obligations/{obligation['id']}/pay").json()
    payment = body["payment"]

    assert payment["obligation_id"] == obligation["id"]
    assert payment["amount"] == 9.99
    assert payment["currency"] == "USD"
    assert payment["paid_at"]


def test_pay_non_active_returns_422(client):
    obligation = _create(client, recurrence=None)
    client.post(f"/obligations/{obligation['id']}/pay")  # -> cancelled

    response = client.post(f"/obligations/{obligation['id']}/pay")
    assert response.status_code == 422

    expired = _create(
        client,
        recurrence=None,
        title="Просроченный",
        next_payment_date=(date.today() - timedelta(days=1)).isoformat(),
    )
    assert client.post(f"/obligations/{expired['id']}/pay").status_code == 422


def test_pay_unknown_obligation_returns_404(client):
    response = client.post(
        "/obligations/00000000-0000-0000-0000-000000000000/pay"
    )
    assert response.status_code == 404
