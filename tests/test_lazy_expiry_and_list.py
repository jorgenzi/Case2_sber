from datetime import date, timedelta

from sqlalchemy import select

from app.models import Obligation, Recurrence, Status
from tests.conftest import obligation_payload


def _seed_overdue(db_session, title: str, recurrence: Recurrence | None,
                  status: Status = Status.active) -> Obligation:
    """Создаёт запись напрямую в БД, минуя правило создания
    (через API просроченная запись сразу стала бы expired)."""
    obligation = Obligation(
        title=title,
        amount=100,
        currency="RUB",
        category="bill",
        recurrence=recurrence,
        next_payment_date=date.today() - timedelta(days=3),
        status=status,
    )
    db_session.add(obligation)
    db_session.commit()
    db_session.refresh(obligation)
    return obligation


def test_lazy_expiry_moves_overdue_one_off_to_expired(client, db_session):
    overdue = _seed_overdue(db_session, "Разовый счёт", recurrence=None)

    response = client.get("/obligations")
    assert response.status_code == 200

    by_id = {o["id"]: o for o in response.json()}
    assert by_id[str(overdue.id)]["status"] == "expired"

    db_session.expire_all()
    assert db_session.get(Obligation, overdue.id).status == Status.expired


def test_lazy_expiry_keeps_recurrent_subscription_active(client, db_session):
    """Ключевое исключение: просроченная рекуррентная подписка остаётся
    active — сервис продолжает действовать, пользователь просто не отметил
    оплату."""
    overdue_sub = _seed_overdue(db_session, "Подписка", recurrence=Recurrence.monthly)

    response = client.get("/obligations")
    by_id = {o["id"]: o for o in response.json()}
    assert by_id[str(overdue_sub.id)]["status"] == "active"


def test_lazy_expiry_does_not_touch_cancelled(client, db_session):
    cancelled = _seed_overdue(
        db_session, "Отменённый", recurrence=None, status=Status.cancelled
    )

    client.get("/obligations")
    db_session.expire_all()
    assert db_session.get(Obligation, cancelled.id).status == Status.cancelled


def test_list_filters_by_category_and_status_combined(client):
    client.post("/obligations", json=obligation_payload(
        title="Netflix", category="subscription"))
    client.post("/obligations", json=obligation_payload(
        title="Страховка", category="insurance", recurrence="yearly"))
    client.post("/obligations", json=obligation_payload(
        title="Старый счёт", category="subscription", recurrence=None,
        next_payment_date=(date.today() - timedelta(days=1)).isoformat()))  # expired

    response = client.get(
        "/obligations", params={"category": "subscription", "status": "active"}
    )
    assert response.status_code == 200
    titles = [o["title"] for o in response.json()]
    assert titles == ["Netflix"]


def test_list_sorted_by_next_payment_date_ascending(client):
    today = date.today()
    for title, offset in [("C", 30), ("A", 5), ("B", 15)]:
        client.post("/obligations", json=obligation_payload(
            title=title,
            next_payment_date=(today + timedelta(days=offset)).isoformat(),
        ))

    response = client.get("/obligations")
    titles = [o["title"] for o in response.json()]
    assert titles == ["A", "B", "C"]
