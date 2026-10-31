"""In-process pub/sub used to push real-time updates over Server-Sent Events."""
from __future__ import annotations

import asyncio
import json
from typing import Any

PUBLIC = "public"      # every signed-in user (bus positions)
OPERATOR = "operator"  # operator dashboard only (reports, alerts)


class EventBroadcaster:
    def __init__(self, queue_size: int = 100) -> None:
        self._subscribers: dict[asyncio.Queue, frozenset[str]] = {}
        self._queue_size = queue_size

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def subscribe(self, audiences: frozenset[str] = frozenset({PUBLIC})) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers[queue] = audiences
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.pop(queue, None)

    async def publish(self, event: str, data: Any, audience: str = PUBLIC) -> None:
        message = format_sse(event, data)
        for queue, audiences in list(self._subscribers.items()):
            if audience not in audiences:
                continue
            if queue.full():  # slow consumer: drop its oldest message instead of blocking everyone
                queue.get_nowait()
            queue.put_nowait(message)


def format_sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str, separators=(',', ':'))}\n\n"
