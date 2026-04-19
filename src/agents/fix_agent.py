from __future__ import annotations

import ast
import json
import re
import time
import uuid
from typing import Any

from .base_agent import BaseAgent, AgentResult, SharedContext
from ..events.schemas import Finding, FixProposal, FixVerification
from ..sandbox.codegen import CodegenTools
from ..sandbox.manager import SandboxManager

FIX_SYSTEM_PROMPT = """You are an expert Python code fixer.

Given a bug or vulnerability finding with surrounding code context, produce a targeted fix.

Respond with a JSON object (no markdown, no explanation outside the JSON):
{
  "original_code": "the exact line(s) from the code context that need replacing",
  "proposed_fix": "the corrected replacement code",
  "explanation": "one sentence explaining why this fixes the issue",
  "confidence": 0.0-1.0
}

CRITICAL RULES:
1. `original_code` MUST be an exact substring of the code shown — copy it character-for-character including all whitespace and indentation.
2. `proposed_fix` MUST be syntactically complete and valid Python on its own.
3. If the fix introduces a block statement (with/if/for/def/class), you MUST include the full block — the header AND all the body lines that belong inside it. Never emit a block header without its body.
   - WRONG: `with open(path) as f:`  (missing body)
   - RIGHT:  `with open(path) as f:\n    data = f.read()`
4. When converting `f = open(...)` + subsequent code to a `with` block, include the subsequent lines (the body) inside the with-block in `proposed_fix`, and include those same body lines in `original_code` so they get replaced together.
5. Keep the same base indentation as the original code.
6. Minimal diff — only change what is necessary to fix the issue.
"""


class FixAgent(BaseAgent):
    def system_prompt(self) -> str:
        return FIX_SYSTEM_PROMPT

    async def analyze(self, context: SharedContext) -> AgentResult:
        return AgentResult(agent_id=self.agent_id)

    async def apply_fixes(
        self,
        findings: list[Finding],
        context: SharedContext,
        sandbox: Any = None,
    ) -> AgentResult:
        await self.emitter.agent_started()

        proposals: list[FixProposal] = []
        verifications: list[FixVerification] = []

        if sandbox is None:
            sandbox = await self.sandbox_manager.get_or_create_sandbox(
                context.session_id, self.agent_id
            )

        # Write all source files into sandbox
        for filename, content in context.files.items():
            await self.sandbox_manager.write_file(sandbox, f"/tmp/{filename}", content)

        for finding in findings:
            await self.emitter.thinking(
                f"Generating fix for {finding.category} in {finding.file}:{finding.line}"
            )
            # Always use the current (possibly already-patched) source
            proposal = await self._propose_fix(finding, context)
            if not proposal:
                continue

            proposals.append(proposal)
            await self.emitter.fix_proposed(proposal.model_dump())

            verification = await self._verify_fix(proposal, finding, context, sandbox)
            verifications.append(verification)
            await self.emitter.fix_verified(verification.model_dump())

            # Sync updated file back to sandbox after successful fix
            if verification.verification_passed:
                updated = context.files.get(finding.file, "")
                await self.sandbox_manager.write_file(sandbox, f"/tmp/{finding.file}", updated)

        await self.emitter.agent_completed(len(proposals))
        return AgentResult(
            agent_id=self.agent_id,
            fix_proposals=proposals,
            fix_verifications=verifications,
        )

    async def _propose_fix(self, finding: Finding, context: SharedContext) -> FixProposal | None:
        source = context.files.get(finding.file, "")
        lines = source.splitlines()
        # Wide context window (±12 lines) so GPT sees full blocks with their bodies
        start = max(0, finding.line - 6)
        end = min(len(lines), finding.line + 12)
        numbered = "\n".join(f"{i+start+1:3d}: {lines[i+start]}" for i in range(end - start))

        await self.emitter.tool_call_start(
            "openai.fix_generation",
            {"file": finding.file, "line": finding.line, "category": finding.category},
        )
        t0 = time.monotonic()

        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI()
            response = await client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": FIX_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Finding: {finding.category} — {finding.description}\n"
                            f"File: {finding.file}, Line: {finding.line}\n\n"
                            f"Code context (with line numbers):\n{numbered}\n\n"
                            f"The original_code field MUST be an exact substring from the code above."
                        ),
                    },
                ],
                temperature=0,
            )
            raw = response.choices[0].message.content or "{}"
        except Exception as exc:
            await self.emitter.error(f"Fix generation failed: {exc}")
            return None

        duration_ms = int((time.monotonic() - t0) * 1000)
        await self.emitter.tool_call_result("openai.fix_generation", raw[:200], duration_ms)

        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group())
            original = data.get("original_code", "").strip()
            proposed = data.get("proposed_fix", "").strip()
            if not original or not proposed:
                return None

            # Pre-validate: apply the fix to source and check syntax before returning
            source = context.files.get(finding.file, "")
            candidate = FixProposal(
                finding_id=finding.finding_id,
                original_code=original,
                proposed_fix=proposed,
                explanation=data.get("explanation", ""),
                confidence=float(data.get("confidence", 0.5)),
                file=finding.file,
                line=finding.line,
            )
            patched = self._apply_fix(source, candidate)
            if patched == source:
                await self.emitter.thinking(
                    f"Skipping fix: original_code not found in source for {finding.file}:{finding.line}"
                )
                return None
            try:
                ast.parse(patched)
            except SyntaxError as e:
                # Retry once: tell the LLM what went wrong
                await self.emitter.thinking(f"Fix produced SyntaxError ({e}), retrying...")
                candidate = await self._retry_fix(finding, context, candidate, str(e))
                if candidate is None:
                    return None
                patched = self._apply_fix(context.files.get(finding.file, ""), candidate)
                try:
                    ast.parse(patched)
                except SyntaxError as e2:
                    await self.emitter.thinking(f"Skipping fix after retry: {e2}")
                    return None
            return candidate
        except Exception:
            return None

    async def _retry_fix(
        self, finding: Finding, context: SharedContext,
        bad: FixProposal, error: str
    ) -> FixProposal | None:
        source = context.files.get(finding.file, "")
        lines = source.splitlines()
        start = max(0, finding.line - 6)
        end = min(len(lines), finding.line + 12)
        numbered = "\n".join(f"{i+start+1:3d}: {lines[i+start]}" for i in range(end - start))
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI()
            response = await client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": FIX_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Finding: {finding.category} — {finding.description}\n"
                            f"File: {finding.file}, Line: {finding.line}\n\n"
                            f"Code context:\n{numbered}\n\n"
                            f"Your previous fix attempt was REJECTED with SyntaxError: {error}\n"
                            f"Previous original_code: {bad.original_code!r}\n"
                            f"Previous proposed_fix: {bad.proposed_fix!r}\n\n"
                            "Provide a corrected fix. Make sure proposed_fix is syntactically complete."
                        ),
                    },
                ],
                temperature=0,
            )
            raw = response.choices[0].message.content or "{}"
        except Exception:
            return None
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group())
            original = data.get("original_code", "").strip()
            proposed = data.get("proposed_fix", "").strip()
            if not original or not proposed:
                return None
            return FixProposal(
                finding_id=finding.finding_id,
                original_code=original,
                proposed_fix=proposed,
                explanation=data.get("explanation", ""),
                confidence=float(data.get("confidence", 0.5)),
                file=finding.file,
                line=finding.line,
            )
        except Exception:
            return None

    async def _verify_fix(
        self,
        proposal: FixProposal,
        finding: Finding,
        context: SharedContext,
        sandbox,
    ) -> FixVerification:
        t0 = time.monotonic()

        # Apply the fix: try exact replace first, fall back to line-based replace
        source = context.files.get(finding.file, "")
        patched = self._apply_fix(source, proposal)

        if patched == source:
            # Fix couldn't be applied — syntax check the original as fallback
            duration_ms = int((time.monotonic() - t0) * 1000)
            return FixVerification(
                finding_id=finding.finding_id,
                verification_passed=False,
                test_output="Fix could not be applied: original_code not found in source",
                duration_ms=duration_ms,
            )

        # Update in-memory files
        context.files[finding.file] = patched
        await self.agent_drive.write(
            f"/review/{context.session_id}/src/{finding.file}", patched
        )

        # Write patched file to sandbox for syntax check
        tmp_path = f"/tmp/{finding.file}"
        await self.sandbox_manager.write_file(sandbox, tmp_path, patched)

        # Syntax check via py_compile
        result = await self.sandbox_manager.exec_with_streaming(
            sandbox,
            f"python3 -m py_compile {tmp_path} 2>&1 && echo '__SYNTAX_OK__'",
            self.emitter,
            "py_compile",
        )

        duration_ms = int((time.monotonic() - t0) * 1000)
        output = (result.stdout + result.stderr).strip()
        passed = "__SYNTAX_OK__" in output or (result.success and not result.stderr.strip())

        if not passed:
            # Rollback
            context.files[finding.file] = source
            await self.agent_drive.restore_snapshot(context.session_id, finding.file)

        return FixVerification(
            finding_id=finding.finding_id,
            verification_passed=passed,
            test_output=output[:500],
            duration_ms=duration_ms,
        )

    def _apply_fix(self, source: str, proposal: FixProposal) -> str:
        original = proposal.original_code
        replacement = proposal.proposed_fix
        source_lines = source.splitlines()

        # 1. Exact match
        if original in source:
            return source.replace(original, replacement, 1)

        # 2. Normalize-and-match: collapse internal whitespace variations
        #    (tabs→spaces, CRLF→LF, trailing spaces, single vs double quotes)
        def normalize(s: str) -> str:
            return "\n".join(
                line.rstrip().expandtabs(4)
                for line in s.splitlines()
            )

        norm_source = normalize(source)
        norm_orig   = normalize(original)
        if norm_orig in norm_source:
            idx = norm_source.index(norm_orig)
            # Map character index back to line numbers in original source
            prefix_lines = norm_source[:idx].count("\n")
            orig_line_count = norm_orig.count("\n") + 1
            indent = len(source_lines[prefix_lines]) - len(source_lines[prefix_lines].lstrip())
            indent_str = " " * indent
            repl_lines = [
                indent_str + l.lstrip() if l.strip() else l
                for l in replacement.splitlines()
            ]
            new_lines = (
                source_lines[:prefix_lines]
                + repl_lines
                + source_lines[prefix_lines + orig_line_count:]
            )
            return "\n".join(new_lines)

        # 3. Strip-normalized line-by-line match (ignores indentation entirely)
        orig_lines = original.splitlines()
        orig_stripped = [l.strip() for l in orig_lines if l.strip()]
        if orig_stripped:
            for i in range(len(source_lines) - len(orig_stripped) + 1):
                window = [source_lines[i + j].strip() for j in range(len(orig_stripped))]
                if window == orig_stripped:
                    indent = len(source_lines[i]) - len(source_lines[i].lstrip())
                    indent_str = " " * indent
                    repl_lines = [
                        indent_str + l.lstrip() if l.strip() else l
                        for l in replacement.splitlines()
                    ]
                    new_lines = source_lines[:i] + repl_lines + source_lines[i + len(orig_stripped):]
                    return "\n".join(new_lines)

        # 4. Last resort: replace just the target line by line number
        target_line = proposal.line - 1
        if 0 <= target_line < len(source_lines):
            indent = len(source_lines[target_line]) - len(source_lines[target_line].lstrip())
            indent_str = " " * indent
            repl_lines = [indent_str + l.lstrip() if l.strip() else l for l in replacement.splitlines()]
            new_lines = source_lines[:target_line] + repl_lines + source_lines[target_line + 1:]
            return "\n".join(new_lines)

        return source
