"""Тесты работают без внешних зависимостей: вместо PostgreSQL
используется in-memory SQLite (модели написаны на дилект-независимых
типах SQLAlchemy 2.0), внешних вызовов у сервиса нет.
"""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models import Base

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True)
def fresh_database():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client():
    # context manager запускает lifespan (инициализация SSE-брокера)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def db_session():
    session = TestingSessionLocal()
    yield session
    session.close()


def obligation_payload(**overrides) -> dict:
    """Фабрика валидного тела POST /obligations."""
    payload = {
        "title": "Яндекс.Плюс",
        "amount": 399.00,
        "currency": "RUB",
        "category": "subscription",
        "recurrence": "monthly",
        "next_payment_date": (date.today() + timedelta(days=10)).isoformat(),
    }
    payload.update(overrides)
    return payload
