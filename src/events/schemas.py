from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from pydantic import BaseModel, field_serializer


class EventType(StrEnum):
    PLAN_CREATED = "plan_created"
    AGENT_DELEGATED = "agent_delegated"
    AGENT_STARTED = "agent_started"
    THINKING = "thinking"
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_RESULT = "tool_call_result"
    FINDING_DISCOVERED = "finding_discovered"
    FIX_PROPOSED = "fix_proposed"
    FIX_VERIFIED = "fix_verified"
    AGENT_COMPLETED = "agent_completed"
    FINDINGS_CONSOLIDATED = "findings_consolidated"
    REVIEW_COMPLETED = "review_completed"
    ERROR = "error"


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class AgentState(StrEnum):
    IDLE = "idle"
    THINKING = "thinking"
    TOOL_CALLING = "tool_calling"
    COMPLETED = "completed"
    ERROR = "error"


class AgentEvent(BaseModel):
    event_type: EventType
    agent_id: str
    timestamp: datetime
    session_id: str
    data: dict[str, Any]

    @field_serializer("timestamp")
    def serialize_ts(self, ts: datetime) -> str:
        return ts.isoformat()

    @field_serializer("event_type")
    def serialize_et(self, et: EventType) -> str:
        return str(et)

    def to_sse(self) -> str:
        return f"data: {self.model_dump_json()}\n\n"

    @classmethod
    def make(
        cls,
        event_type: EventType,
        agent_id: str,
        session_id: str,
        data: dict[str, Any],
    ) -> "AgentEvent":
        return cls(
            event_type=event_type,
            agent_id=agent_id,
            session_id=session_id,
            timestamp=datetime.now(timezone.utc),
            data=data,
        )


class Finding(BaseModel):
    finding_id: str
    file: str
    line: int
    severity: Severity
    category: str
    description: str
    agent_id: str
    code_snippet: str = ""

    def to_dict(self) -> dict:
        return self.model_dump()


class FixProposal(BaseModel):
    finding_id: str
    original_code: str
    proposed_fix: str
    explanation: str
    confidence: float
    file: str
    line: int


class FixVerification(BaseModel):
    finding_id: str
    verification_passed: bool
    test_output: str
    duration_ms: int


class PlanStep(BaseModel):
    step: int
    description: str
    status: str = "pending"


class ReviewResult(BaseModel):
    session_id: str
    total_findings: int
    findings_by_severity: dict[str, int]
    fixes_proposed: int
    fixes_verified: int
    fix_success_rate: float
    findings: list[Finding]
    fix_proposals: list[FixProposal]
    fix_verifications: list[FixVerification]
    duration_ms: int
    fixed_files: dict[str, str] = {}  # filename -> fixed source code
