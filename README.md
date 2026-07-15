# Умный реестр подписок

Backend-ядро платформы учёта подписок и регулярных платежей: обязательства (подписки, счета, гарантии, страховки), расчёт даты следующего списания с учётом календарных особенностей, lazy expiry для просроченных разовых обязательств и SSE-события для реал-тайм обновления интерфейса.

## Возможности

- CRUD обязательств: создание, список с фильтрами, отмена, удаление
- Фиксация оплаты со сдвигом `next_payment_date` для рекуррентных обязательств
- Lazy expiry — просроченные разовые обязательства переводятся в `expired` при чтении, без фонового планировщика
- Корректный сдвиг дат на границах месяцев и високосных годов (`dateutil.relativedelta`)
- `GET /obligations/upcoming` — обязательства на ближайшие N дней, суммы по валютам, алерты о скором списании по подпискам
- `GET /events` — SSE-поток (сейчас: удаление обязательства)
- Запуск одной командой через Docker Compose с автоприменением миграций

## Стек

Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, PostgreSQL 16, python-dateutil, pytest + httpx, Docker Compose.

Модели написаны на диалект-независимых типах SQLAlchemy (`Enum(..., native_enum=False)`, `Uuid`, `Numeric`) — это позволяет гонять тесты на in-memory SQLite без адаптации моделей и без реального Postgres. `dateutil.relativedelta` вместо ручной арифметики с `timedelta` — единственный вменяемый способ прибавить календарный месяц/год без ручной обработки границ (31 января, 29 февраля).

## Архитектура

```
app/
  models.py           SQLAlchemy ORM: Obligation, Payment, enum'ы Category/Recurrence/Status
  schemas.py          Pydantic-схемы запросов/ответов
  services.py         чистая бизнес-логика: shift_next_payment, apply_lazy_expiry
  routers/
    obligations.py    FastAPI-роутер — HTTP-слой поверх services/models
  sse.py              in-memory брокер SSE
  database.py         engine, сессии, get_db()
  config.py           настройки из .env
  main.py             сборка приложения
alembic/               миграции
tests/                 unit (shift_next_payment) + API-тесты через TestClient
```

`services.py` не знает про FastAPI и HTTP-статусы — `shift_next_payment` и `apply_lazy_expiry` принимают только домен (`date`, `Recurrence`, `Session`) и тестируются напрямую, без поднятия сервера.

## API

| Метод | Путь | Описание |
|---|---|---|
| POST | `/obligations` | Создать обязательство |
| GET | `/obligations` | Список обязательств (фильтры `category`, `status`) |
| GET | `/obligations/upcoming` | Обязательства в окне `[today, today+days]`, суммы по валютам, алерты по подпискам |
| POST | `/obligations/{id}/pay` | Зафиксировать оплату |
| PATCH | `/obligations/{id}/cancel` | Отменить обязательство |
| DELETE | `/obligations/{id}` | Удалить обязательство (каскадно с историей платежей) |
| GET | `/events` | SSE-поток событий |
| GET | `/health` | Health check |

Swagger: http://localhost:8000/docs

## Запуск

```bash
git clone <repo-url>
cd Case2_sber
docker compose up --build
```

`db` (Postgres) поднимается с healthcheck, `app` стартует после того как БД готова. `entrypoint.sh` перед запуском сервера прогоняет `alembic upgrade head` — миграции применяются автоматически.

```bash
curl http://localhost:8000/health   # {"status": "ok"}
docker compose down                 # остановить, данные сохранятся
docker compose down -v              # остановить и снести volume с БД
```

### Без Docker

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # DATABASE_URL с host=localhost вместо db
alembic upgrade head
uvicorn app.main:app --reload
```

Нужен локально поднятый Postgres — `docker compose up db` поднимет только БД.

## Тесты

БД не нужна: `tests/conftest.py` подменяет `get_db` на сессию `sqlite://` (in-memory, `StaticPool`).

```bash
docker compose exec app pytest -v
# или локально, в venv из раздела выше:
pytest -v
```

Что покрыто:

- `test_shift_next_payment_handles_calendar_edges` — граничные случаи дат (31.01, 29.02, конец квартала)
- `test_create_obligation.py` — дата в прошлом → сразу `expired`, предупреждение о дубле по названию, валидация payload
- `test_pay.py` — сдвиг даты от `next_payment_date`, а не от даты оплаты; закрытие разовых обязательств; 422/404
- `test_cancel_delete_upcoming.py` — отмена, каскадное удаление, публикация SSE, окно `/upcoming`
- `test_lazy_expiry_and_list.py` — в `expired` уходят только разовые просроченные, рекуррентные и уже `cancelled` не трогаются

## Почему lazy expiry не трогает рекуррентные обязательства

`apply_lazy_expiry` переводит в `expired` только записи с `recurrence IS NULL` и просроченной `next_payment_date`.

Для разового обязательства `next_payment_date` — единственный дедлайн: если дата прошла, а оплата не отмечена, обязательство больше не актуально (счёт просрочен, гарантия истекла) — `expired` тут корректен.

Для рекуррентного (подписка) просроченная дата означает не «умерла», а «пользователь не подтвердил очередной платёж» — сервис (Netflix и т.п.) списывает деньги по расписанию независимо от учётной записи в этой системе. Перевод в `expired` тут был бы семантической ошибкой: после `/pay` дата просто сдвинется на следующий период, подписка остаётся `active`. Отменить её явно всё равно можно — через `PATCH /cancel`, это осознанное действие пользователя, а не автоматика по дате.

## Граничные случаи с датами

Сдвиг — в `shift_next_payment` (`app/services.py`), через `dateutil.relativedelta`, а не `timedelta(days=30)`.

Два момента:

- Сдвиг всегда считается от текущего `next_payment_date`, а не от фактической даты оплаты — иначе при регулярно ранней/поздней оплате накапливалось бы смещение на весь срок жизни подписки.
- `relativedelta` сам приводит несуществующие даты к последнему дню месяца: 31.01 + месяц → 28/29.02, 31.03 + месяц → 30.04, 30.11 + 3 месяца → 28/29.02 следующего года, 29.02 високосного + год → 28.02. Все кейсы закреплены параметризованным тестом в `tests/test_pay.py`.

Побочный эффект: если дата уже съехала на конец месяца, она там и останется при следующих сдвигах, а не «вернётся» на исходное число — это поведение `relativedelta`, а не баг.

## Компромиссы

- Lazy expiry вместо фонового джоба — статус пересчитывается синхронно при чтении (`GET /obligations`, `/upcoming`). Проще, но запись формально просрочена, пока её никто не прочитал. Если понадобится независимый от чтения пересчёт (вебхуки, отчёты) — нужен отдельный шедулер.
- SSE-брокер in-memory, одна очередь на подписчика в процессе. Работает для одного инстанса; при нескольких репликах подписчик на одной не увидит событие с другой — известное ограничение, вынесено на будущее в Redis Pub/Sub.
- Конвертации валют нет — `totals` в `/upcoming` суммируются отдельно по каждой валюте.
- Нет аутентификации и многопользовательности — общее пространство обязательств.
- История платежей пишется, но не отдаётся отдельным эндпоинтом — только косвенно через `/pay`.
- Дубли по названию — только предупреждение, не блокировка: ожидаемый сценарий при парсинге писем/чеков AI-модулем, жёсткий запрет давал бы ложные отказы.

При наличии большего времени: вынести SSE на Redis Pub/Sub, добавить фоновый пересчёт `expired` и эндпоинт со статистикой, ввести `user_id`/авторизацию, добавить идемпотентность для `POST /obligations` (сейчас повторный запрос создаёт вторую запись с warning вместо переиспользования первой), курсы валют для агрегированных сумм.

## Примеры запросов

```bash
# разовый счёт
curl -X POST http://localhost:8000/obligations \
  -H "Content-Type: application/json" \
  -d '{"title": "Счёт за интернет", "amount": 690.00, "currency": "RUB", "category": "bill", "recurrence": null, "next_payment_date": "2026-08-01"}'

# рекуррентная подписка
curl -X POST http://localhost:8000/obligations \
  -H "Content-Type: application/json" \
  -d '{"title": "Яндекс.Плюс", "amount": 399.00, "currency": "RUB", "category": "subscription", "recurrence": "monthly", "next_payment_date": "2026-07-20"}'

curl "http://localhost:8000/obligations?category=subscription&status=active"
curl "http://localhost:8000/obligations/upcoming?days=14"
curl -X POST http://localhost:8000/obligations/<obligation_id>/pay
curl -X PATCH http://localhost:8000/obligations/<obligation_id>/cancel
curl -X DELETE http://localhost:8000/obligations/<obligation_id>
curl -N http://localhost:8000/events
```

Postman-коллекции в репозитории нет — для ручного тестирования используйте Swagger UI на http://localhost:8000/docs.
