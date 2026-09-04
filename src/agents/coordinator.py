from __future__ import annotations

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
from .agentic_loop import CoordinatorLoop
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
        """Run the review as an agentic loop rather than a fixed script.

        The coordinator decides what to inspect, which specialists to
        dispatch and with what focus, which findings are real, and whether
        a failed fix is worth retrying. This method only sets up the
        participants and turns the loop's outcome into a result payload.
        """
        start = time.monotonic()

        await self.agent_drive.upload_sources(context.session_id, context.files)
        await self.emitter.agent_started()

        specialists, fix_agent = self._build_agents(context, event_bus)
        shared_sandbox = await self.sandbox_manager.get_or_create_sandbox(
            context.session_id, "shared"
        )
        for filename in context.files:
            await self.agent_drive.snapshot_source(context.session_id, filename)

        loop = CoordinatorLoop(
            coordinator=self,
            context=context,
            specialists=specialists,
            fix_agent=fix_agent,
            sandbox=shared_sandbox,
        )
        await loop.run()

        findings = self._deduplicate(list(loop.findings.values()))
        context.existing_findings = findings

        await self.emitter.emit(EventType.FINDINGS_CONSOLIDATED, {
            "total": len(findings),
            "by_severity": self._count_by_severity(findings),
            "findings": [f.to_dict() for f in findings],
            "dismissed": [
                {"finding_id": fid, "reason": reason}
                for fid, reason in loop.dismissed.items()
            ],
        })
        await self.emitter.agent_completed(len(findings))

        duration_ms = int((time.monotonic() - start) * 1000)
        result = self._build_result(
            context.session_id,
            findings,
            loop.fix_proposals,
            loop.fix_verifications,
            duration_ms,
            context.files,
        )
        payload = result.model_dump(mode="json", by_alias=True, exclude_none=False)
        payload["telemetry"] = self.telemetry.summary()
        payload["summary"] = loop.summary
        payload["dismissed"] = [
            {"finding_id": fid, "reason": reason}
            for fid, reason in loop.dismissed.items()
        ]
        await self.emitter.emit(EventType.REVIEW_COMPLETED, payload)
        return result

    def _build_agents(
        self, context: SharedContext, event_bus: Any
    ) -> tuple[dict[str, BaseAgent], FixAgent]:
        """Construct the specialists and the fix agent for one review."""
        def make(agent_cls, agent_id: str):
            return agent_cls(
                agent_id,
                EventEmitter(agent_id, context.session_id, event_bus),
                self.sandbox_manager,
                self.agent_drive,
                telemetry=self.telemetry,
            )

        specialists: dict[str, BaseAgent] = {
            "security": make(SecurityAgent, "security"),
            "bug_detection": make(BugDetectionAgent, "bug_detection"),
        }
        fix_agent = make(FixAgent, "fix")

        if self._mcp_manager.configured:
            for agent in (*specialists.values(), fix_agent):
                agent.mcp_manager = self._mcp_manager

        return specialists, fix_agent

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
