from __future__ import annotations

import asyncio
import time
from typing import Any

from ..events.emitter import EventEmitter
from ..events.schemas import (
    EventType,
    Finding,
    FixProposal,
    FixVerification,
    PlanStep,
    ReviewResult,
    Severity,
)
from ..events.telemetry import ReviewTelemetry
from ..sandbox.manager import SandboxManager
from ..storage.agent_drive import AgentDrive
from .base_agent import AgentResult, BaseAgent, SharedContext
from .bug_agent import BugDetectionAgent
from .fix_agent import FixAgent
from .security_agent import SecurityAgent


class CoordinatorAgent(BaseAgent):
    def system_prompt(self) -> str:
        return "You are the coordinator of a multi-agent code review system."

    def __init__(
        self,
        emitter: EventEmitter,
        sandbox_manager: SandboxManager,
        agent_drive: AgentDrive,
        session_id: str,
    ) -> None:
        # Coordinator doesn't need its own emitter constructor from base; build directly
        self.agent_id = "coordinator"
        self.emitter = emitter
        self.sandbox_manager = sandbox_manager
        self.agent_drive = agent_drive
        self._session_id = session_id
        self.telemetry = ReviewTelemetry(session_id=session_id)

    async def analyze(self, context: SharedContext) -> AgentResult:
        raise NotImplementedError("Use run_review() instead")

    async def run_review(
        self, context: SharedContext, event_bus: Any
    ) -> ReviewResult:
        # MCP manager is created up front and closed in the finally block so
        # server processes never outlive the review, even on failure.
        from ..mcp_tools import MCPServerManager
        self._mcp_manager = MCPServerManager()
        try:
            return await self._run_review(context, event_bus)
        except Exception as exc:
            await self.emitter.error(f"Review failed: {exc}")
            raise
        finally:
            await self._mcp_manager.aclose()

    async def _run_review(
        self, context: SharedContext, event_bus: Any
    ) -> ReviewResult:
        start = time.monotonic()

        # Step 1: Upload sources to Agent Drive
        await self.agent_drive.upload_sources(context.session_id, context.files)

        # Step 2: Create and emit plan
        plan_steps = self._build_plan(context)
        await self.emitter.emit(EventType.PLAN_CREATED, {
            "steps": [s.model_dump() for s in plan_steps],
            "files": list(context.files.keys()),
            "agents": ["security", "bug_detection", "fix"],
        })
        await self.agent_drive.write_plan(context.session_id, {
            "steps": [s.model_dump() for s in plan_steps],
        })

        # Step 3: Create specialist agents
        security_emitter = EventEmitter("security", context.session_id, event_bus)
        bug_emitter = EventEmitter("bug_detection", context.session_id, event_bus)
        fix_emitter = EventEmitter("fix", context.session_id, event_bus)

        security_agent = SecurityAgent(
            "security", security_emitter, self.sandbox_manager, self.agent_drive,
            telemetry=self.telemetry,
        )
        bug_agent = BugDetectionAgent(
            "bug_detection", bug_emitter, self.sandbox_manager, self.agent_drive,
            telemetry=self.telemetry,
        )
        fix_agent = FixAgent(
            "fix", fix_emitter, self.sandbox_manager, self.agent_drive,
            telemetry=self.telemetry,
        )

        # Attach MCP server manager: specialists gain per-agent context tools
        # (filesystem, git, dependency data) declared in mcp_servers.json.
        if self._mcp_manager.configured:
            await self.emitter.thinking(
                "MCP servers configured — attaching context tools to specialist agents"
            )
        for specialist in (security_agent, bug_agent, fix_agent):
            specialist.mcp_manager = self._mcp_manager

        # Step 4: Delegate to Security + Bug agents in parallel
        await self.emitter.emit(EventType.AGENT_DELEGATED, {
            "agents": ["security", "bug_detection"],
        })
        await self.emitter.thinking("Delegating to Security and Bug Detection agents in parallel...")

        results = await asyncio.gather(
            security_agent.analyze(context),
            bug_agent.analyze(context),
            return_exceptions=True,
        )

        all_findings: list[Finding] = []
        for result in results:
            if isinstance(result, Exception):
                await self.emitter.error(f"Specialist agent error: {result}")
            elif isinstance(result, AgentResult):
                all_findings.extend(result.findings)

        # Step 5: Deduplicate and prioritize findings
        deduped = self._deduplicate(all_findings)
        await self.emitter.thinking(
            f"Consolidated {len(all_findings)} raw findings → {len(deduped)} unique findings"
        )

        # Step 6: Snapshot source files before applying fixes (for rollback)
        for filename in context.files:
            await self.agent_drive.snapshot_source(context.session_id, filename)

        # Delegate to Fix Agent — reuse the shared sandbox (already created for session)
        await self.emitter.emit(EventType.AGENT_DELEGATED, {"agents": ["fix"]})
        context.existing_findings = deduped
        shared_sandbox = await self.sandbox_manager.get_or_create_sandbox(
            context.session_id, "shared"
        )
        fix_result = await fix_agent.apply_fixes(deduped, context, sandbox=shared_sandbox)

        # Step 7: Emit consolidated findings
        await self.emitter.emit(EventType.FINDINGS_CONSOLIDATED, {
            "total": len(deduped),
            "by_severity": self._count_by_severity(deduped),
            "findings": [f.to_dict() for f in deduped],
        })

        # Mark coordinator itself as completed so the UI updates
        await self.emitter.agent_completed(len(deduped))

        # Step 8: Emit completion payload
        duration_ms = int((time.monotonic() - start) * 1000)

        result = self._build_result(
            context.session_id,
            deduped,
            fix_result.fix_proposals,
            fix_result.fix_verifications,
            duration_ms,
            context.files,
        )
        payload = result.model_dump(mode="json", by_alias=True, exclude_none=False)
        payload["telemetry"] = self.telemetry.summary()
        await self.emitter.emit(EventType.REVIEW_COMPLETED, payload)
        return result

    def _build_plan(self, context: SharedContext) -> list[PlanStep]:
        return [
            PlanStep(step=1, description=f"Parse {len(context.files)} file(s)"),
            PlanStep(step=2, description="Security analysis (parallel)"),
            PlanStep(step=3, description="Bug detection (parallel)"),
            PlanStep(step=4, description="Apply & verify fixes"),
            PlanStep(step=5, description="Consolidate findings"),
        ]

    def _deduplicate(self, findings: list[Finding]) -> list[Finding]:
        seen: dict[tuple, Finding] = {}
        for f in findings:
            # Bucket line numbers to the nearest 2 so near-duplicate findings collapse
            key = (f.file, round(f.line / 2) * 2, f.category)
            if key not in seen:
                seen[key] = f
            else:
                # Keep the one with higher severity
                existing = seen[key]
                severity_rank = {
                    Severity.CRITICAL: 4, Severity.HIGH: 3,
                    Severity.MEDIUM: 2, Severity.LOW: 1, Severity.INFO: 0
                }
                if severity_rank.get(f.severity, 0) > severity_rank.get(existing.severity, 0):
                    seen[key] = f
        severity_order = {
            Severity.CRITICAL: 0, Severity.HIGH: 1,
            Severity.MEDIUM: 2, Severity.LOW: 3, Severity.INFO: 4
        }
        return sorted(seen.values(), key=lambda x: (severity_order.get(x.severity, 5), x.file, x.line))

    def _count_by_severity(self, findings: list[Finding]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for f in findings:
            counts[str(f.severity)] = counts.get(str(f.severity), 0) + 1
        return counts

    def _build_result(
        self,
        session_id: str,
        findings: list[Finding],
        proposals: list[FixProposal],
        verifications: list[FixVerification],
        duration_ms: int,
        fixed_files: dict[str, str],
    ) -> ReviewResult:
        verified_count = sum(1 for v in verifications if v.verification_passed)
        fix_rate = verified_count / len(proposals) if proposals else 0.0
        return ReviewResult(
            session_id=session_id,
            total_findings=len(findings),
            findings_by_severity=self._count_by_severity(findings),
            fixes_proposed=len(proposals),
            fixes_verified=verified_count,
            fix_success_rate=fix_rate,
            findings=findings,
            fix_proposals=proposals,
            fix_verifications=verifications,
            duration_ms=duration_ms,
            fixed_files=fixed_files,
        )
