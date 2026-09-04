"""Per-agent token, latency, and cost telemetry.

Each agent's LLM calls are recorded here so a completed review can report
what it cost and how long each specialist took. Prices are per million
tokens and are kept in one table so a model change is a one-line edit.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field

# USD per 1M tokens, (input, output).
_PRICING: dict[str, tuple[float, float]] = {
    "google/gemini-2.5-pro": (1.25, 10.00),
    "google/gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-2.5-flash": (0.30, 2.50),
    "gpt-4o": (2.50, 10.00),
}
_DEFAULT_PRICING = (0.0, 0.0)


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """USD cost for one call; 0.0 for models with no published price here."""
    input_price, output_price = _PRICING.get(model, _DEFAULT_PRICING)
    return (prompt_tokens * input_price + completion_tokens * output_price) / 1_000_000


@dataclass
class AgentUsage:
    """Rolled-up LLM usage for a single agent within one review."""

    agent_id: str
    model: str = ""
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0

    def record(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: int,
        reasoning_tokens: int = 0,
    ) -> None:
        self.model = model or self.model
        self.calls += 1
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.reasoning_tokens += reasoning_tokens
        self.total_tokens += prompt_tokens + completion_tokens
        self.latency_ms += latency_ms
        self.cost_usd += estimate_cost(model, prompt_tokens, completion_tokens)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["cost_usd"] = round(self.cost_usd, 6)
        return data


@dataclass
class ReviewTelemetry:
    """Telemetry for every agent taking part in one review session."""

    session_id: str
    agents: dict[str, AgentUsage] = field(default_factory=dict)
    started_at: float = field(default_factory=time.monotonic)

    def usage_for(self, agent_id: str) -> AgentUsage:
        if agent_id not in self.agents:
            self.agents[agent_id] = AgentUsage(agent_id=agent_id)
        return self.agents[agent_id]

    def record_response(self, agent_id: str, model: str, response, latency_ms: int) -> None:
        """Record usage from an OpenAI-style response (safe if absent)."""
        self.record_usage(agent_id, model, getattr(response, "usage", None), latency_ms)

    def record_usage(self, agent_id: str, model: str, usage, latency_ms: int) -> None:
        """Record a provider usage object (safe if absent)."""
        if usage is None:
            return
        details = getattr(usage, "completion_tokens_details", None)
        self.usage_for(agent_id).record(
            model=model,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            reasoning_tokens=getattr(details, "reasoning_tokens", 0) or 0 if details else 0,
            latency_ms=latency_ms,
        )

    def summary(self) -> dict:
        agents = [u.to_dict() for u in self.agents.values()]
        return {
            "session_id": self.session_id,
            "agents": agents,
            "totals": {
                "calls": sum(a["calls"] for a in agents),
                "prompt_tokens": sum(a["prompt_tokens"] for a in agents),
                "completion_tokens": sum(a["completion_tokens"] for a in agents),
                "reasoning_tokens": sum(a["reasoning_tokens"] for a in agents),
                "total_tokens": sum(a["total_tokens"] for a in agents),
                "cost_usd": round(sum(a["cost_usd"] for a in agents), 6),
                "wall_clock_ms": int((time.monotonic() - self.started_at) * 1000),
            },
        }
