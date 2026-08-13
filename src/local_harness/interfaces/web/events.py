"""Bounded in-memory delivery of browser lifecycle events."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import asdict, replace

from local_harness.domain.web_ui import WebEvent


class WebEventHub:
    """Broadcast bounded events and retain a short reconnect window."""

    def __init__(self, *, history_limit: int = 500, queue_limit: int = 200) -> None:
        """Configure replay and subscriber queue bounds."""
        self._history: deque[WebEvent] = deque(maxlen=history_limit)
        self._queues: dict[str, asyncio.Queue[WebEvent]] = {}
        self._queue_limit = queue_limit
        self._next_id = 1
        self._lock = asyncio.Lock()

    async def publish(self, event: WebEvent) -> WebEvent:
        """Assign an ID, retain, and broadcast one event."""
        async with self._lock:
            assigned = replace(event, event_id=self._next_id)
            self._next_id += 1
            self._history.append(assigned)
            for client_id, queue in tuple(self._queues.items()):
                if queue.full():
                    while not queue.empty():
                        queue.get_nowait()
                    queue.put_nowait(
                        WebEvent(
                            event_id=self._next_id,
                            type="resync_required",
                            payload={"reason": "client queue exceeded its limit"},
                        )
                    )
                    self._next_id += 1
                    del self._queues[client_id]
                    continue
                queue.put_nowait(assigned)
            return assigned

    async def subscribe(self, client_id: str, after_event_id: int) -> asyncio.Queue[WebEvent]:
        """Register one client and enqueue retained newer events."""
        async with self._lock:
            queue: asyncio.Queue[WebEvent] = asyncio.Queue(maxsize=self._queue_limit)
            for event in self._history:
                if event.event_id > after_event_id and not queue.full():
                    queue.put_nowait(event)
            self._queues[client_id] = queue
            return queue

    async def unsubscribe(self, client_id: str) -> None:
        """Remove one subscriber without affecting persisted session events."""
        async with self._lock:
            self._queues.pop(client_id, None)

    @staticmethod
    def serialize(event: WebEvent) -> dict[str, object]:
        """Return a JSON-compatible event envelope."""
        return asdict(event)
