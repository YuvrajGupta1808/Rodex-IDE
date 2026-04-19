from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum

from ..agents.base_agent import SharedContext
from ..events.schemas import ReviewResult


class SessionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ReviewSession:
    session_id: str
    context: SharedContext
    status: SessionStatus = SessionStatus.PENDING
    result: ReviewResult | None = None
    error: str | None = None

    @classmethod
    def create(cls, files: dict[str, str]) -> "ReviewSession":
        session_id = str(uuid.uuid4())
        return cls(
            session_id=session_id,
            context=SharedContext(session_id=session_id, files=files),
        )
