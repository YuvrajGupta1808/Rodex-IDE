from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import AsyncGenerator

from .schemas import AgentEvent


class AsyncEventBus:
    def __init__(self) -> None:
        # session_id -> list of subscriber queues
        self._subscribers: dict[str, list[asyncio.Queue[AgentEvent | None]]] = defaultdict(list)
        # session_id -> replay buffer (for reconnecting clients)
        self._history: dict[str, list[AgentEvent]] = defaultdict(list)

    async def publish(self, event: AgentEvent) -> None:
        self._history[event.session_id].append(event)
        queues = self._subscribers.get(event.session_id, [])
        for q in queues:
            await q.put(event)

    async def subscribe(
        self, session_id: str, replay_from: int = 0
    ) -> AsyncGenerator[AgentEvent, None]:
        q: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
        self._subscribers[session_id].append(q)
        try:
            # replay missed events
            for event in self._history[session_id][replay_from:]:
                yield event
            # stream live events
            while True:
                event = await q.get()
                if event is None:
                    break
                yield event
        finally:
            self._subscribers[session_id].remove(q)

    async def close_session(self, session_id: str) -> None:
        for q in self._subscribers.get(session_id, []):
            await q.put(None)

    def history_len(self, session_id: str) -> int:
        return len(self._history.get(session_id, []))

    def clear_history(self, session_id: str) -> None:
        self._history.pop(session_id, None)
