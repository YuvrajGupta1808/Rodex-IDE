from __future__ import annotations

import time
from typing import Any

from .bus import AsyncEventBus
from .schemas import AgentEvent, EventType


class EventEmitter:
    def __init__(self, agent_id: str, session_id: str, bus: AsyncEventBus) -> None:
        self.agent_id = agent_id
        self.session_id = session_id
        self._bus = bus

    async def emit(self, event_type: EventType, data: dict[str, Any]) -> None:
        event = AgentEvent.make(event_type, self.agent_id, self.session_id, data)
        await self._bus.publish(event)

    async def thinking(self, text: str) -> None:
        await self.emit(EventType.THINKING, {"text": text})

    async def agent_started(self) -> None:
        await self.emit(EventType.AGENT_STARTED, {"state": "thinking"})

    async def agent_completed(self, finding_count: int) -> None:
        await self.emit(EventType.AGENT_COMPLETED, {"finding_count": finding_count, "state": "completed"})

    async def tool_call_start(self, tool_name: str, inputs: dict[str, Any]) -> None:
        await self.emit(EventType.TOOL_CALL_START, {"tool_name": tool_name, "inputs": inputs, "state": "tool_calling"})

    async def tool_call_result(self, tool_name: str, output: Any, duration_ms: int) -> None:
        await self.emit(EventType.TOOL_CALL_RESULT, {
            "tool_name": tool_name,
            "output": output if isinstance(output, str) else str(output)[:500],
            "duration_ms": duration_ms,
            "state": "thinking",
        })

    async def finding(self, finding_data: dict[str, Any]) -> None:
        await self.emit(EventType.FINDING_DISCOVERED, finding_data)

    async def fix_proposed(self, fix_data: dict[str, Any]) -> None:
        await self.emit(EventType.FIX_PROPOSED, fix_data)

    async def fix_verified(self, verification_data: dict[str, Any]) -> None:
        await self.emit(EventType.FIX_VERIFIED, verification_data)

    async def error(self, message: str) -> None:
        await self.emit(EventType.ERROR, {"message": message, "state": "error"})
