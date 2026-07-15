import asyncio
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from app.routers.obligations import router as obligations_router
from app.sse import broker

KEEPALIVE_INTERVAL_SECONDS = 15.0


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Сохраняем event loop, чтобы синхронные эндпоинты (threadpool)
    # могли потокобезопасно публиковать SSE-события.
    broker.set_loop(asyncio.get_running_loop())
    yield


app = FastAPI(
    title="Умный реестр подписок",
    description=(
        "Backend-ядро платформы управления личными подписками и "
        "регулярными платежами: учёт обязательств, расчёт дат списаний "
        "с учётом календарных особенностей, lazy expiry и SSE-события."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(obligations_router)


@app.get("/events", tags=["events"], summary="SSE-поток событий")
async def sse_events():
    """Server-Sent Events. Сейчас транслируется одно событие:

    `{"type": "obligation_deleted", "id": "<uuid>"}` — после DELETE
    /obligations/{id}, чтобы фронтенд обновил интерфейс в реальном времени.
    """
    queue = broker.subscribe()

    async def event_stream():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=KEEPALIVE_INTERVAL_SECONDS
                    )
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    # комментарий-keepalive, чтобы прокси не рвали соединение
                    yield ": keep-alive\n\n"
        finally:
            broker.unsubscribe(queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/health", tags=["service"], summary="Health check")
def health():
    return {"status": "ok"}
