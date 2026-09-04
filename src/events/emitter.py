from __future__ import annotations

from typing import Any

from .bus import AsyncEventBus
from .schemas import AgentEvent, EventType


class EventEmitter:
    def __init__(self, agent_id: str, session_id: str, bus: AsyncEventBus) -> None:
        self.agent_id = agent_id
        self.session_id = session_id
        self._bus = bus
        # Set by the coordinator while a step is open, so tool calls can be
        # attributed to the step whose work produced them.
        self.step_id: str | None = None

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
        await self.emit(EventType.TOOL_CALL_START, {
            "tool_name": tool_name,
            "inputs": inputs,
            "state": "tool_calling",
            "step_id": self.step_id,
        })

    async def tool_call_result(self, tool_name: str, output: Any, duration_ms: int) -> None:
        await self.emit(EventType.TOOL_CALL_RESULT, {
            "tool_name": tool_name,
            "output": output if isinstance(output, str) else str(output)[:500],
            "duration_ms": duration_ms,
            "state": "thinking",
            "step_id": self.step_id,
        })

    async def finding(self, finding_data: dict[str, Any]) -> None:
        await self.emit(EventType.FINDING_DISCOVERED, finding_data)

    async def fix_proposed(self, fix_data: dict[str, Any]) -> None:
        await self.emit(EventType.FIX_PROPOSED, fix_data)

    async def fix_verified(self, verification_data: dict[str, Any]) -> None:
        await self.emit(EventType.FIX_VERIFIED, verification_data)

    async def reasoning(self, text: str) -> None:
        """The coordinator's own deliberation, distinct from an agent's."""
        await self.emit(EventType.COORDINATOR_REASONING, {"text": text})

    async def step_started(self, step_id: str, title: str, detail: str = "") -> None:
        await self.emit(
            EventType.STEP_STARTED,
            {"step_id": step_id, "title": title, "detail": detail},
        )

    async def step_completed(
        self, step_id: str, outcome: str, summary: str = ""
    ) -> None:
        """Close a step. ``outcome`` is one of ok | failed | skipped."""
        await self.emit(
            EventType.STEP_COMPLETED,
            {"step_id": step_id, "outcome": outcome, "summary": summary},
        )

    async def decision(self, choice: str, rationale: str, options: Any = None) -> None:
        """A branch the coordinator chose, and why."""
        await self.emit(
            EventType.DECISION_MADE,
            {"choice": choice, "rationale": rationale, "options": options or []},
        )

    async def retry_scheduled(
        self, target: str, attempt: int, max_attempts: int, reason: str
    ) -> None:
        await self.emit(
            EventType.RETRY_SCHEDULED,
            {
                "target": target,
                "attempt": attempt,
                "max_attempts": max_attempts,
                "reason": reason,
            },
        )

    async def telemetry(self, summary: dict[str, Any]) -> None:
        """A running cost/usage snapshot, so the UI can show it live."""
        await self.emit(EventType.TELEMETRY_UPDATED, summary)

    async def error(self, message: str) -> None:
        await self.emit(EventType.ERROR, {"message": message, "state": "error"})
