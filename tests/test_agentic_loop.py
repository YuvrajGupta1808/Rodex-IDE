"""Tests for the coordinator's control loop.

These cover the decisions the loop owns — dispatching, dismissing,
retrying a failed fix, and refusing unsafe commands — without calling a
real model.
"""

import pytest

from src.agents.agentic_loop import CoordinatorLoop
from src.agents.orchestration import MAX_FIX_ATTEMPTS
from src.events.schemas import Finding, FixProposal, FixVerification, Severity


class _Emitter:
    def __init__(self):
        self.events = []

    async def _record(self, kind, **payload):
        self.events.append((kind, payload))

    async def reasoning(self, text):
        await self._record("reasoning", text=text)

    async def step_started(self, step_id, title, detail=""):
        await self._record("step_started", step_id=step_id, title=title, detail=detail)

    async def step_completed(self, step_id, outcome, summary=""):
        await self._record(
            "step_completed", step_id=step_id, outcome=outcome, summary=summary
        )

    async def decision(self, choice, rationale, options=None):
        await self._record("decision", choice=choice, rationale=rationale)

    async def retry_scheduled(self, target, attempt, max_attempts, reason):
        await self._record("retry", target=target, attempt=attempt, reason=reason)

    async def error(self, message):
        await self._record("error", message=message)

    async def tool_call_start(self, *args, **kwargs):
        pass

    async def tool_call_result(self, *args, **kwargs):
        pass

    def kinds(self):
        return [kind for kind, _ in self.events]


class _Context:
    def __init__(self):
        self.session_id = "s1"
        self.files = {"m.py": "import sqlite3\nx = 1\n"}
        self.metadata = {}
        self.existing_findings = []


class _Coordinator:
    def __init__(self, emitter):
        self.emitter = emitter
        self.telemetry = type("T", (), {"record_usage": lambda *a, **k: None})()
        self.sandbox_manager = None


def _finding(finding_id="f1"):
    return Finding(
        finding_id=finding_id,
        file="m.py",
        line=1,
        severity=Severity.CRITICAL,
        category="sql_injection",
        description="f-string in query",
        agent_id="security",
    )


class _Specialist:
    def __init__(self, findings):
        self._findings = findings
        self.seen_focus = None
        self.emitter = _Emitter()

    async def analyze(self, context):
        self.seen_focus = context.metadata.get("focus")
        return type("R", (), {"findings": self._findings})()


class _FixAgent:
    """Fails the first ``fail_times`` attempts, then verifies."""

    def __init__(self, fail_times=0):
        self.fail_times = fail_times
        self.calls = 0
        self.guidance_seen = []
        self.emitter = _Emitter()

    async def apply_fixes(self, findings, context, sandbox=None):
        self.calls += 1
        self.guidance_seen.append(context.metadata.get("fix_guidance"))
        passed = self.calls > self.fail_times
        finding = findings[0]
        return type(
            "R",
            (),
            {
                "fix_proposals": [
                    FixProposal(
                        finding_id=finding.finding_id,
                        original_code="x",
                        proposed_fix="y",
                        explanation="",
                        confidence=0.9,
                        file="m.py",
                        line=1,
                    )
                ],
                "fix_verifications": [
                    FixVerification(
                        finding_id=finding.finding_id,
                        verification_passed=passed,
                        test_output="" if passed else "__PATTERN_REMAINS__",
                        duration_ms=1,
                    )
                ],
            },
        )()


def _loop(specialist=None, fix_agent=None):
    emitter = _Emitter()
    return (
        CoordinatorLoop(
            coordinator=_Coordinator(emitter),
            context=_Context(),
            specialists={"security": specialist or _Specialist([])},
            fix_agent=fix_agent or _FixAgent(),
            sandbox=object(),
        ),
        emitter,
    )


@pytest.mark.asyncio
async def test_inspect_code_returns_numbered_source():
    loop, _ = _loop()

    out = await loop._dispatch({"name": "inspect_code", "arguments": "{}"})

    assert "=== m.py ===" in out
    assert "  1  import sqlite3" in out


@pytest.mark.asyncio
async def test_dispatching_a_specialist_passes_the_focus_through():
    specialist = _Specialist([_finding()])
    loop, emitter = _loop(specialist=specialist)

    out = await loop._dispatch(
        {
            "name": "run_specialist",
            "arguments": '{"agent": "security", "focus": "the f-string on line 4"}',
        }
    )

    assert specialist.seen_focus == "the f-string on line 4"
    assert "1 finding(s)" in out
    assert "step_started" in emitter.kinds()
    assert "decision" in emitter.kinds()
    assert loop.findings["f1"].category == "sql_injection"


@pytest.mark.asyncio
async def test_dismissing_a_finding_removes_it_from_the_result():
    loop, emitter = _loop(specialist=_Specialist([_finding()]))
    await loop._dispatch(
        {"name": "run_specialist", "arguments": '{"agent":"security","focus":"x"}'}
    )

    await loop._dispatch(
        {
            "name": "dismiss_finding",
            "arguments": '{"finding_id":"f1","reason":"guarded on the same line"}',
        }
    )

    assert "f1" not in loop.findings
    assert loop.dismissed["f1"] == "guarded on the same line"


@pytest.mark.asyncio
async def test_a_failed_fix_reports_why_and_invites_a_retry():
    fix_agent = _FixAgent(fail_times=1)
    loop, emitter = _loop(specialist=_Specialist([_finding()]), fix_agent=fix_agent)
    await loop._dispatch(
        {"name": "run_specialist", "arguments": '{"agent":"security","focus":"x"}'}
    )

    out = await loop._dispatch({"name": "apply_fix", "arguments": '{"finding_id":"f1"}'})

    assert "did NOT verify" in out
    assert "__PATTERN_REMAINS__" in out
    assert "retry" in out
    assert ("step_completed", {"step_id": "step-2", "outcome": "failed",
                               "summary": "__PATTERN_REMAINS__"}) in emitter.events


@pytest.mark.asyncio
async def test_a_retry_carries_guidance_and_is_announced():
    fix_agent = _FixAgent(fail_times=1)
    loop, emitter = _loop(specialist=_Specialist([_finding()]), fix_agent=fix_agent)
    await loop._dispatch(
        {"name": "run_specialist", "arguments": '{"agent":"security","focus":"x"}'}
    )
    await loop._dispatch({"name": "apply_fix", "arguments": '{"finding_id":"f1"}'})

    out = await loop._dispatch(
        {
            "name": "apply_fix",
            "arguments": '{"finding_id":"f1","guidance":"use a parameterised query"}',
        }
    )

    assert "verified" in out
    assert fix_agent.guidance_seen[-1] == "use a parameterised query"
    assert "retry" in emitter.kinds()


@pytest.mark.asyncio
async def test_attempts_are_bounded():
    fix_agent = _FixAgent(fail_times=99)
    loop, _ = _loop(specialist=_Specialist([_finding()]), fix_agent=fix_agent)
    await loop._dispatch(
        {"name": "run_specialist", "arguments": '{"agent":"security","focus":"x"}'}
    )

    for _ in range(MAX_FIX_ATTEMPTS):
        await loop._dispatch({"name": "apply_fix", "arguments": '{"finding_id":"f1"}'})
    out = await loop._dispatch({"name": "apply_fix", "arguments": '{"finding_id":"f1"}'})

    assert "already been attempted" in out
    assert fix_agent.calls == MAX_FIX_ATTEMPTS


@pytest.mark.asyncio
async def test_an_unsafe_command_is_refused_without_touching_the_sandbox():
    loop, emitter = _loop()

    out = await loop._dispatch(
        {"name": "run_command", "arguments": '{"command":"rm -rf /","purpose":"x"}'}
    )

    assert out.startswith("Refused")
    # sandbox_manager is None, so reaching it at all would raise.
    assert "decision" in emitter.kinds()


@pytest.mark.asyncio
async def test_unknown_tool_is_reported_rather_than_raising():
    loop, _ = _loop()

    out = await loop._dispatch({"name": "nonexistent", "arguments": "{}"})

    assert "Unknown tool" in out


@pytest.mark.asyncio
async def test_malformed_arguments_do_not_crash_the_loop():
    loop, _ = _loop()

    out = await loop._dispatch({"name": "inspect_code", "arguments": "{not json"})

    assert "=== m.py ===" in out


class _SlowSpecialist:
    """Records when it ran, so overlap can be asserted."""

    def __init__(self, findings, delay=0.05, log=None):
        self._findings = findings
        self._delay = delay
        self.log = log if log is not None else []
        self.emitter = _Emitter()

    async def analyze(self, context):
        import asyncio as _asyncio

        self.log.append(("start", id(self)))
        await _asyncio.sleep(self._delay)
        self.log.append(("end", id(self)))
        return type("R", (), {"findings": self._findings})()


@pytest.mark.asyncio
async def test_specialists_dispatched_together_run_concurrently():
    """Regression: the agentic rewrite serialised the specialists.

    The scripted coordinator ran them with asyncio.gather; the tool loop
    awaited each call in turn, so a review paid for both sequentially —
    measured at nearly three minutes of dead wall-clock in one run.
    """
    import asyncio as _asyncio

    log = []
    emitter = _Emitter()
    loop = CoordinatorLoop(
        coordinator=_Coordinator(emitter),
        context=_Context(),
        specialists={
            "security": _SlowSpecialist([_finding("a")], log=log),
            "bug_detection": _SlowSpecialist([_finding("b")], log=log),
        },
        fix_agent=_FixAgent(),
        sandbox=object(),
    )

    calls = [
        {"id": "1", "name": "run_specialist",
         "arguments": '{"agent":"security","focus":"x"}'},
        {"id": "2", "name": "run_specialist",
         "arguments": '{"agent":"bug_detection","focus":"y"}'},
    ]
    started = _asyncio.get_running_loop().time()
    results = await loop._dispatch_batch(calls)
    elapsed = _asyncio.get_running_loop().time() - started

    assert len(results) == 2
    # Both started before either finished — genuine overlap, not interleaving.
    assert [kind for kind, _ in log[:2]] == ["start", "start"]
    # Two 0.05s agents run together take well under the 0.1s serial cost.
    assert elapsed < 0.09
    assert set(loop.findings) == {"a", "b"}


@pytest.mark.asyncio
async def test_results_stay_aligned_with_their_tool_calls():
    """Parallel dispatch must not reorder results relative to calls."""
    loop, _ = _loop(specialist=_Specialist([_finding()]))

    calls = [
        {"id": "1", "name": "inspect_code", "arguments": "{}"},
        {"id": "2", "name": "run_specialist",
         "arguments": '{"agent":"security","focus":"x"}'},
    ]
    results = await loop._dispatch_batch(calls)

    assert "=== m.py ===" in results[0]
    assert "security returned" in results[1]


@pytest.mark.asyncio
async def test_one_failing_specialist_does_not_sink_the_batch():
    class _Boom:
        emitter = _Emitter()

        async def analyze(self, context):
            raise RuntimeError("specialist crashed")

    emitter = _Emitter()
    loop = CoordinatorLoop(
        coordinator=_Coordinator(emitter),
        context=_Context(),
        specialists={"security": _Specialist([_finding()]), "bug_detection": _Boom()},
        fix_agent=_FixAgent(),
        sandbox=object(),
    )

    results = await loop._dispatch_batch([
        {"id": "1", "name": "run_specialist",
         "arguments": '{"agent":"security","focus":"x"}'},
        {"id": "2", "name": "run_specialist",
         "arguments": '{"agent":"bug_detection","focus":"y"}'},
    ])

    assert "security returned" in results[0]
    assert "failed" in results[1].lower()
    # The healthy specialist's findings survive.
    assert "f1" in loop.findings


@pytest.mark.asyncio
async def test_fixes_are_not_run_concurrently():
    """Fixes mutate shared file contents, so they must stay serialised."""
    order = []

    class _OrderedFix(_FixAgent):
        async def apply_fixes(self, findings, context, sandbox=None):
            import asyncio as _asyncio

            order.append(("start", findings[0].finding_id))
            await _asyncio.sleep(0.02)
            order.append(("end", findings[0].finding_id))
            return await super().apply_fixes(findings, context, sandbox)

    loop, _ = _loop(specialist=_Specialist([_finding("a"), _finding("b")]),
                    fix_agent=_OrderedFix())
    await loop._dispatch(
        {"name": "run_specialist", "arguments": '{"agent":"security","focus":"x"}'}
    )

    await loop._dispatch_batch([
        {"id": "1", "name": "apply_fix", "arguments": '{"finding_id":"a"}'},
        {"id": "2", "name": "apply_fix", "arguments": '{"finding_id":"b"}'},
    ])

    # Each fix completes before the next begins.
    assert order == [("start", "a"), ("end", "a"), ("start", "b"), ("end", "b")]


@pytest.mark.asyncio
async def test_fixes_to_different_files_run_in_parallel():
    """Fixes to disjoint files need not wait for one another.

    Same-file fixes must stay ordered (each must see the previous patch),
    but serialising every fix made a multi-file review pay for them one at
    a time.
    """
    import asyncio as _asyncio

    log = []

    class _TrackingFix:
        emitter = _Emitter()

        async def apply_fixes(self, findings, context, sandbox=None):
            finding = findings[0]
            log.append(("start", finding.file))
            await _asyncio.sleep(0.05)
            log.append(("end", finding.file))
            return type("R", (), {
                "fix_proposals": [],
                "fix_verifications": [
                    FixVerification(
                        finding_id=finding.finding_id,
                        verification_passed=True,
                        test_output="",
                        duration_ms=1,
                    )
                ],
            })()

    emitter = _Emitter()
    loop = CoordinatorLoop(
        coordinator=_Coordinator(emitter),
        context=_Context(),
        specialists={},
        fix_agent=_TrackingFix(),
        sandbox=object(),
    )
    for finding_id, filename in (("a", "one.py"), ("b", "two.py")):
        finding = _finding(finding_id)
        finding.file = filename
        loop.findings[finding_id] = finding

    started = _asyncio.get_running_loop().time()
    await loop._dispatch_batch([
        {"id": "1", "name": "apply_fix", "arguments": '{"finding_id":"a"}'},
        {"id": "2", "name": "apply_fix", "arguments": '{"finding_id":"b"}'},
    ])
    elapsed = _asyncio.get_running_loop().time() - started

    # Both files start before either finishes.
    assert [kind for kind, _ in log[:2]] == ["start", "start"]
    assert elapsed < 0.09
