"""Tests for per-agent token, latency, and cost telemetry."""

from src.events.telemetry import ReviewTelemetry, estimate_cost


class _Details:
    reasoning_tokens = 200


class _Usage:
    prompt_tokens = 1_000
    completion_tokens = 500
    completion_tokens_details = _Details()


class _Response:
    usage = _Usage()


def test_cost_uses_per_model_pricing():
    # 1M input tokens at $1.25 for gemini-2.5-pro.
    assert estimate_cost("google/gemini-2.5-pro", 1_000_000, 0) == 1.25
    assert estimate_cost("unknown-model", 1_000_000, 1_000_000) == 0.0


def test_records_usage_per_agent():
    telemetry = ReviewTelemetry(session_id="s1")
    telemetry.record_response("security", "google/gemini-2.5-pro", _Response(), 1_200)

    summary = telemetry.summary()
    agent = summary["agents"][0]

    assert agent["agent_id"] == "security"
    assert agent["total_tokens"] == 1_500
    assert agent["reasoning_tokens"] == 200
    assert agent["latency_ms"] == 1_200
    assert agent["cost_usd"] > 0


def test_totals_aggregate_across_agents():
    telemetry = ReviewTelemetry(session_id="s1")
    telemetry.record_response("security", "google/gemini-2.5-pro", _Response(), 1_000)
    telemetry.record_response("bug_detection", "google/gemini-2.5-pro", _Response(), 900)

    totals = telemetry.summary()["totals"]

    assert totals["calls"] == 2
    assert totals["total_tokens"] == 3_000


def test_missing_usage_is_ignored():
    telemetry = ReviewTelemetry(session_id="s1")
    telemetry.record_response("security", "m", object(), 10)
    telemetry.record_usage("security", "m", None, 10)

    assert telemetry.summary()["totals"]["calls"] == 0
