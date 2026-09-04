"""The coordinator's agentic control loop.

Runs the coordinator as an LLM tool-calling loop: it reasons about the
code, dispatches specialists with a focus it chose, judges what returns,
retries fixes that fail verification, and decides when to stop. Every
decision is emitted so the UI can show the reasoning rather than a
fixed progress bar.

The loop owns control flow only. The specialists and the fix agent do the
actual work, exactly as before.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from ..events.schemas import Finding
from .orchestration import (
    COORDINATOR_SYSTEM_PROMPT,
    MAX_FIX_ATTEMPTS,
    MAX_ITERATIONS,
    build_tools,
    command_is_allowed,
)

logger = logging.getLogger(__name__)

_MAX_TOOL_RESULT_CHARS = 4000

# Tools that only read the source and share no mutable state, so several
# may run at once. Everything else is serialised.
_CONCURRENT_TOOLS = frozenset({"run_specialist", "inspect_code"})


def _numbered(source: str) -> str:
    return "\n".join(f"{i + 1:3d}  {line}" for i, line in enumerate(source.splitlines()))


class CoordinatorLoop:
    """Drives one review as a sequence of coordinator tool calls."""

    def __init__(self, coordinator: Any, context: Any, specialists: dict[str, Any],
                 fix_agent: Any, sandbox: Any) -> None:
        self._coordinator = coordinator
        self._emitter = coordinator.emitter
        self._context = context
        self._specialists = specialists
        self._fix_agent = fix_agent
        self._sandbox = sandbox

        self.findings: dict[str, Finding] = {}
        self.dismissed: dict[str, str] = {}
        self.fix_proposals: list[Any] = []
        self.fix_verifications: list[Any] = []
        self.summary: str = ""
        self._fix_attempts: dict[str, int] = {}
        self._step = 0

    # ── loop ────────────────────────────────────────────────────────────

    async def run(self) -> None:
        from .llm_client import (
            get_client,
            get_model,
            thinking_extra_body,
            tool_name,
        )
        from .streaming import stream_reasoning

        client = get_client()
        model = get_model()
        tools = build_tools()

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": COORDINATOR_SYSTEM_PROMPT},
            {"role": "user", "content": self._opening_prompt()},
        ]

        for iteration in range(MAX_ITERATIONS):
            label = tool_name("plan")
            started = time.monotonic()
            await self._emitter.tool_call_start(
                label, {"model": model, "iteration": iteration + 1}
            )

            stream = await client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                stream=True,
                stream_options={"include_usage": True},
                extra_body=thinking_extra_body(),
            )

            text, tool_calls, usage = await stream_reasoning(stream, self._emitter)
            duration_ms = int((time.monotonic() - started) * 1000)
            self._coordinator.telemetry.record_usage(
                "coordinator", model, usage, duration_ms
            )
            await self._emitter.tool_call_result(
                label,
                f"{len(tool_calls)} action(s) chosen"
                if tool_calls
                else "no action chosen",
                duration_ms,
            )
            await self._publish_telemetry()

            if not tool_calls:
                # The model answered in prose instead of acting. Treat a
                # final message as the review's summary and stop.
                if text.strip():
                    self.summary = text.strip()
                return

            messages.append(
                {
                    "role": "assistant",
                    "content": text or None,
                    "tool_calls": [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {
                                "name": call["name"],
                                "arguments": call["arguments"],
                            },
                        }
                        for call in tool_calls
                    ],
                }
            )

            finish = next(
                (c for c in tool_calls if c["name"] == "finish_review"), None
            )
            if finish is not None:
                self.summary = _parse_args(finish["arguments"]).get("summary", "")
                await self._emitter.reasoning(self.summary or "Review complete.")
                return

            results = await self._dispatch_batch(tool_calls)
            for call, result in zip(tool_calls, results, strict=True):
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": result[:_MAX_TOOL_RESULT_CHARS],
                    }
                )

        await self._emitter.reasoning(
            "Reached the maximum number of coordination steps; "
            "concluding with what has been established."
        )

    async def _publish_telemetry(self) -> None:
        """Push a running cost snapshot so the UI updates during the review."""
        try:
            await self._emitter.telemetry(self._coordinator.telemetry.summary())
        except Exception:  # noqa: BLE001 - telemetry must never break a review
            logger.debug("telemetry publish failed", exc_info=True)

    # ── tool dispatch ───────────────────────────────────────────────────

    async def _dispatch_batch(self, calls: list[dict[str, Any]]) -> list[str]:
        """Run a turn's tool calls, concurrently where that is safe.

        Specialists analyse the same immutable source and share nothing, so
        running them together is both safe and the point — a review that
        dispatches security and bug detection should not pay for them twice
        in wall-clock time.

        Fixes are grouped by file: two fixes to different files touch
        disjoint state and run together, while fixes to the same file stay
        in order because each must see what the previous one did.

        Everything else is serialised.
        """
        concurrent = [c for c in calls if c["name"] in _CONCURRENT_TOOLS]
        fixes = [c for c in calls if c["name"] == "apply_fix"]
        serial = [
            c for c in calls
            if c["name"] not in _CONCURRENT_TOOLS and c["name"] != "apply_fix"
        ]

        results: dict[int, str] = {}
        if len(concurrent) > 1:
            await self._emitter.decision(
                choice=f"run {len(concurrent)} specialists in parallel",
                rationale="they analyse the same source and share no state",
            )

        if concurrent:
            gathered = await asyncio.gather(
                *(self._dispatch(call) for call in concurrent),
                return_exceptions=True,
            )
            for call, outcome in zip(concurrent, gathered, strict=True):
                results[id(call)] = (
                    f"Tool {call['name']} failed: {outcome}"
                    if isinstance(outcome, BaseException)
                    else outcome
                )

        if fixes:
            results.update(await self._dispatch_fixes(fixes))

        for call in serial:
            results[id(call)] = await self._dispatch(call)

        return [results[id(call)] for call in calls]

    async def _dispatch_fixes(self, calls: list[dict[str, Any]]) -> dict[int, str]:
        """Apply fixes, parallel across files and sequential within one."""
        by_file: dict[str, list[dict[str, Any]]] = {}
        for call in calls:
            finding_id = _parse_args(call["arguments"]).get("finding_id", "")
            finding = self.findings.get(finding_id)
            by_file.setdefault(finding.file if finding else "", []).append(call)

        if len(by_file) > 1:
            await self._emitter.decision(
                choice=f"fix {len(by_file)} files in parallel",
                rationale="fixes to different files touch disjoint state",
            )

        async def run_group(group: list[dict[str, Any]]) -> dict[int, str]:
            out: dict[int, str] = {}
            for call in group:
                out[id(call)] = await self._dispatch(call)
            return out

        grouped = await asyncio.gather(
            *(run_group(group) for group in by_file.values()),
            return_exceptions=True,
        )
        results: dict[int, str] = {}
        for group, outcome in zip(by_file.values(), grouped, strict=True):
            if isinstance(outcome, BaseException):
                for call in group:
                    results[id(call)] = f"Fix failed: {outcome}"
            else:
                results.update(outcome)
        return results

    async def _dispatch(self, call: dict[str, Any]) -> str:
        name = call["name"]
        args = _parse_args(call["arguments"])
        handlers = {
            "inspect_code": self._inspect_code,
            "run_specialist": self._run_specialist,
            "dismiss_finding": self._dismiss_finding,
            "apply_fix": self._apply_fix,
            "run_command": self._run_command,
        }
        handler = handlers.get(name)
        if handler is None:
            return f"Unknown tool {name!r}."
        try:
            return await handler(args)
        except Exception as exc:  # noqa: BLE001 - reported to the model, not fatal
            logger.exception("coordinator tool %s failed", name)
            await self._emitter.error(f"{name} failed: {exc}")
            return f"Tool {name} failed: {exc}"

    async def _inspect_code(self, args: dict[str, Any]) -> str:
        wanted = args.get("file")
        files = self._context.files
        if wanted and wanted in files:
            files = {wanted: files[wanted]}
        return "\n\n".join(
            f"=== {name} ===\n{_numbered(source)}" for name, source in files.items()
        )

    async def _run_specialist(self, args: dict[str, Any]) -> str:
        agent_id = args.get("agent", "")
        focus = args.get("focus", "")
        specialist = self._specialists.get(agent_id)
        if specialist is None:
            return f"No specialist named {agent_id!r}."

        step_id = self._next_step()
        await self._emitter.step_started(
            step_id, f"{agent_id.replace('_', ' ').title()} analysis", focus
        )
        _tag_step(specialist, step_id)
        await self._emitter.decision(
            choice=f"run {agent_id}",
            rationale=focus,
            options=list(self._specialists),
        )

        self._context.metadata["focus"] = focus
        result = await specialist.analyze(self._context)
        new = [f for f in result.findings if f.finding_id not in self.findings]
        for finding in new:
            self.findings[finding.finding_id] = finding

        _tag_step(specialist, None)
        await self._emitter.step_completed(
            step_id, "ok", f"{len(new)} finding(s)"
        )
        await self._publish_telemetry()
        if not new:
            return f"{agent_id} found nothing."
        return f"{agent_id} returned {len(new)} finding(s):\n" + _describe(new)

    async def _dismiss_finding(self, args: dict[str, Any]) -> str:
        finding_id = args.get("finding_id", "")
        reason = args.get("reason", "")
        if finding_id not in self.findings:
            return f"No open finding with id {finding_id!r}."
        finding = self.findings.pop(finding_id)
        self.dismissed[finding_id] = reason
        await self._emitter.decision(
            choice=f"dismiss {finding.category} at {finding.file}:{finding.line}",
            rationale=reason,
        )
        return f"Dismissed {finding_id}."

    async def _apply_fix(self, args: dict[str, Any]) -> str:
        finding_id = args.get("finding_id", "")
        guidance = args.get("guidance", "")
        finding = self.findings.get(finding_id)
        if finding is None:
            return f"No open finding with id {finding_id!r}."

        attempt = self._fix_attempts.get(finding_id, 0) + 1
        self._fix_attempts[finding_id] = attempt
        if attempt > MAX_FIX_ATTEMPTS:
            return (
                f"{finding_id} has already been attempted {MAX_FIX_ATTEMPTS} times; "
                "leave it for the user and move on."
            )

        step_id = self._next_step()
        await self._emitter.step_started(
            step_id,
            f"Fix {finding.category} ({finding.file}:{finding.line})",
            guidance or "",
        )
        if attempt > 1:
            await self._emitter.retry_scheduled(
                target=f"{finding.file}:{finding.line}",
                attempt=attempt,
                max_attempts=MAX_FIX_ATTEMPTS,
                reason=guidance or "previous attempt did not verify",
            )

        _tag_step(self._fix_agent, step_id)
        if guidance:
            self._context.metadata["fix_guidance"] = guidance
        result = await self._fix_agent.apply_fixes(
            [finding], self._context, sandbox=self._sandbox
        )
        self._context.metadata.pop("fix_guidance", None)
        _tag_step(self._fix_agent, None)

        self.fix_proposals.extend(result.fix_proposals)
        self.fix_verifications.extend(result.fix_verifications)

        verification = result.fix_verifications[0] if result.fix_verifications else None
        if verification is not None and verification.verification_passed:
            await self._emitter.step_completed(step_id, "ok", "verified")
            return f"Fix for {finding_id} verified and applied."

        detail = (
            verification.test_output
            if verification is not None
            else "no fix could be produced"
        )
        await self._emitter.step_completed(step_id, "failed", detail[:200])
        remaining = MAX_FIX_ATTEMPTS - attempt
        return (
            f"Fix for {finding_id} did NOT verify (attempt {attempt}). "
            f"Reason: {detail[:400]}\n"
            + (
                f"You may retry with better guidance ({remaining} attempt(s) left) "
                "or leave this finding unfixed."
                if remaining > 0
                else "No attempts remain for this finding."
            )
        )

    async def _run_command(self, args: dict[str, Any]) -> str:
        command = args.get("command", "")
        purpose = args.get("purpose", "")
        allowed, why = command_is_allowed(command)
        if not allowed:
            await self._emitter.decision(
                choice="refused command", rationale=f"{command!r}: {why}"
            )
            return f"Refused: {why}."

        result = await self._coordinator.sandbox_manager.exec_with_streaming(
            self._sandbox, command, self._emitter, "sandbox.exec"
        )
        output = (result.stdout + result.stderr).strip()
        return f"({purpose})\n{output or '(no output)'}"

    # ── helpers ─────────────────────────────────────────────────────────

    def _next_step(self) -> str:
        self._step += 1
        return f"step-{self._step}"

    def _opening_prompt(self) -> str:
        listing = "\n\n".join(
            f"=== {name} ===\n{_numbered(source)}"
            for name, source in self._context.files.items()
        )
        return (
            f"Review the following {len(self._context.files)} file(s). "
            "Begin by stating what the code does and where you expect "
            "problems, then dispatch specialists accordingly.\n\n" + listing
        )


def _tag_step(agent: Any, step_id: str | None) -> None:
    """Attribute an agent's tool calls to the step it is working on."""
    emitter = getattr(agent, "emitter", None)
    if emitter is not None:
        emitter.step_id = step_id


def _parse_args(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _describe(findings: list[Finding]) -> str:
    return "\n".join(
        f"- id={f.finding_id} [{f.severity}] {f.category} at {f.file}:{f.line} — "
        f"{f.description}"
        for f in findings
    )
