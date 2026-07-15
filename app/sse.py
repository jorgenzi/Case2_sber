"""Простой in-memory брокер SSE-событий.

Эндпоинты работают синхронно (в threadpool), а SSE-подписчики живут
в event loop, поэтому публикация идёт через call_soon_threadsafe.
Для одного инстанса сервиса этого достаточно; при горизонтальном
масштабировании брокер заменяется на Redis Pub/Sub (см. README).
"""
import asyncio


class EventBroker:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def publish(self, event: dict) -> None:
        """Потокобезопасная публикация события всем подписчикам."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        for queue in list(self._subscribers):
            loop.call_soon_threadsafe(queue.put_nowait, event)


broker = EventBroker()
