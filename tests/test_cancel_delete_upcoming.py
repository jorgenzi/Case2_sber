from datetime import date, timedelta

from sqlalchemy import select

from app.models import Payment
from app.sse import broker
from tests.conftest import TestingSessionLocal, obligation_payload


def _create(client, **overrides) -> dict:
    response = client.post("/obligations", json=obligation_payload(**overrides))
    assert response.status_code == 201
    return response.json()["obligation"]


# ---------- PATCH /cancel ----------

def test_cancel_active_obligation(client):
    obligation = _create(client)

    response = client.patch(f"/obligations/{obligation['id']}/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


def test_cancel_non_active_returns_422_with_message(client):
    obligation = _create(client)
    client.patch(f"/obligations/{obligation['id']}/cancel")

    # уже cancelled
    response = client.patch(f"/obligations/{obligation['id']}/cancel")
    assert response.status_code == 422
    assert "active" in response.json()["detail"]

    # expired
    expired = _create(
        client,
        title="Просроченный",
        recurrence=None,
        next_payment_date=(date.today() - timedelta(days=1)).isoformat(),
    )
    response = client.patch(f"/obligations/{expired['id']}/cancel")
    assert response.status_code == 422


# ---------- DELETE ----------

def test_delete_returns_204_removes_payments_and_publishes_sse(client, monkeypatch):
    published: list[dict] = []
    monkeypatch.setattr(broker, "publish", published.append)

    obligation = _create(client, recurrence="monthly")
    client.post(f"/obligations/{obligation['id']}/pay")  # создаём платёж

    response = client.delete(f"/obligations/{obligation['id']}")
    assert response.status_code == 204

    # каскадное удаление платежей
    with TestingSessionLocal() as session:
        payments = session.scalars(select(Payment)).all()
        assert payments == []

    # SSE-событие
    assert published == [{"type": "obligation_deleted", "id": obligation["id"]}]

    # запись действительно удалена
    assert client.delete(f"/obligations/{obligation['id']}").status_code == 404


def test_delete_works_for_any_status(client):
    obligation = _create(client)
    client.patch(f"/obligations/{obligation['id']}/cancel")

    assert client.delete(f"/obligations/{obligation['id']}").status_code == 204


# ---------- GET /obligations/upcoming ----------

def test_upcoming_window_totals_and_renewal_alerts(client):
    today = date.today()

    in_window_sub = _create(client, title="Netflix", amount=9.99, currency="USD",
                            category="subscription", recurrence="monthly",
                            next_payment_date=(today + timedelta(days=3)).isoformat())
    _create(client, title="ЖКХ", amount=1490.00, currency="RUB",
            category="bill", recurrence=None,
            next_payment_date=(today + timedelta(days=7)).isoformat())
    # вне окна (7 дней по умолчанию)
    _create(client, title="Страховка", amount=5000, currency="RUB",
            category="insurance", recurrence="yearly",
            next_payment_date=(today + timedelta(days=30)).isoformat())
    # разовая ПОДПИСКА в окне: попадает в obligations, но НЕ в renewal_alerts
    _create(client, title="Разовый доступ", amount=5.00, currency="USD",
            category="subscription", recurrence=None,
            next_payment_date=(today + timedelta(days=2)).isoformat())

    response = client.get("/obligations/upcoming")
    assert response.status_code == 200
    body = response.json()

    titles = {o["title"] for o in body["obligations"]}
    assert titles == {"Netflix", "ЖКХ", "Разовый доступ"}

    assert body["totals"] == {"USD": 14.99, "RUB": 1490.00}

    alerts = body["renewal_alerts"]
    assert len(alerts) == 1
    assert alerts[0]["id"] == in_window_sub["id"]
    assert alerts[0]["title"] == "Netflix"
    assert alerts[0]["amount"] == 9.99
    assert alerts[0]["currency"] == "USD"


def test_upcoming_respects_days_parameter(client):
    today = date.today()
    _create(client, title="Далёкий платёж",
            next_payment_date=(today + timedelta(days=20)).isoformat())

    default_window = client.get("/obligations/upcoming").json()
    assert default_window["obligations"] == []

    wide_window = client.get("/obligations/upcoming", params={"days": 25}).json()
    assert [o["title"] for o in wide_window["obligations"]] == ["Далёкий платёж"]


def test_upcoming_rejects_invalid_days(client):
    assert client.get(
        "/obligations/upcoming", params={"days": -1}
    ).status_code == 422
