from datetime import date, timedelta

from app.services import DUPLICATE_WARNING
from tests.conftest import obligation_payload


def test_create_returns_201_and_generated_fields(client):
    response = client.post("/obligations", json=obligation_payload())

    assert response.status_code == 201
    body = response.json()
    obligation = body["obligation"]
    assert obligation["id"]
    assert obligation["status"] == "active"
    assert obligation["title"] == "Яндекс.Плюс"
    assert obligation["amount"] == 399.00
    assert obligation["currency"] == "RUB"
    assert obligation["created_at"] and obligation["updated_at"]
    assert body["warning"] is None


def test_create_with_past_date_gets_expired_status_not_error(client):
    """Правило 1: дата в прошлом — не ошибка, статус сразу expired
    (AI-модуль добавляет старые чеки постфактум)."""
    payload = obligation_payload(
        next_payment_date=(date.today() - timedelta(days=30)).isoformat(),
        recurrence=None,
    )
    response = client.post("/obligations", json=payload)

    assert response.status_code == 201
    assert response.json()["obligation"]["status"] == "expired"


def test_duplicate_active_title_returns_warning_case_insensitive(client):
    """Правило 2: дубль по названию (без учёта регистра) создаётся,
    но с warning в ответе."""
    first = client.post("/obligations", json=obligation_payload(title="Netflix"))
    assert first.status_code == 201
    assert first.json()["warning"] is None

    second = client.post("/obligations", json=obligation_payload(title="NETFLIX"))
    assert second.status_code == 201
    body = second.json()
    assert body["warning"] == DUPLICATE_WARNING
    # запись всё равно создана
    assert body["obligation"]["id"] != first.json()["obligation"]["id"]


def test_no_warning_when_existing_obligation_is_not_active(client):
    """Warning выдаётся только при совпадении с АКТИВНЫМ обязательством."""
    expired = obligation_payload(
        title="Spotify",
        recurrence=None,
        next_payment_date=(date.today() - timedelta(days=5)).isoformat(),
    )
    assert client.post("/obligations", json=expired).status_code == 201  # expired

    response = client.post("/obligations", json=obligation_payload(title="Spotify"))
    assert response.status_code == 201
    assert response.json()["warning"] is None


def test_validation_rejects_bad_payloads(client):
    cases = [
        obligation_payload(amount=-10),                # отрицательная сумма
        obligation_payload(amount=0),                  # нулевая сумма
        obligation_payload(currency="RUBLES"),         # не ISO 4217
        obligation_payload(category="netflix"),        # неизвестная категория
        obligation_payload(recurrence="weekly"),       # неизвестная периодичность
        obligation_payload(title="   "),               # пустое название
    ]
    for payload in cases:
        assert client.post("/obligations", json=payload).status_code == 422, payload


def test_currency_is_normalized_to_uppercase(client):
    response = client.post("/obligations", json=obligation_payload(currency="usd"))
    assert response.status_code == 201
    assert response.json()["obligation"]["currency"] == "USD"
