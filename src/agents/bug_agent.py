from __future__ import annotations

import json
import re

from .base_agent import BaseAgent, AgentResult, SharedContext
from ..events.schemas import Finding, Severity

SYSTEM_PROMPT = """You are a specialized bug detection expert for Python code.

Your job is to find REAL bugs and logic errors. For each issue found, output a JSON array of findings.

Each finding must be a JSON object with these exact fields:
{
  "file": "filename.py",
  "line": <line number as integer>,
  "severity": "critical" | "high" | "medium" | "low",
  "category": "null_reference" | "logic_error" | "type_mismatch" | "race_condition" | "resource_leak" | "off_by_one" | "error_swallowing" | "other",
  "description": "Clear description of the bug",
  "code_snippet": "The buggy line of code"
}

CRITICAL RULES — you MUST follow these before flagging anything:

NEVER flag code that is already safely guarded. Specifically:
- `x.attr if x else default` — this is SAFE. The None check is on the same line. Do NOT flag it.
- `x.attr if x is not None else default` — SAFE. Do NOT flag.
- `x and x.attr` — SAFE boolean short-circuit. Do NOT flag.
- `if x: x.attr` — SAFE. The guard is explicit. Do NOT flag.
- `getattr(x, 'attr', default)` — SAFE. Do NOT flag.
- Any expression where the object is checked for None/falsiness before the attribute access on the same logical line is SAFE.

Only flag null_reference when the attribute or method is accessed on an object that is PROVABLY not guarded — e.g., `return obj.attr` with no None check anywhere on that line.

Focus on:
1. Null/None reference — ONLY unguarded attribute/method access on possibly-None values
2. Logic errors — inverted conditions, wrong boolean operators, incorrect comparisons
3. Off-by-one — loop bounds, array indexing, fence-post errors
4. Type mismatches — implicit int/str conversion, wrong dict key types
5. Race conditions — shared mutable state accessed across threads without locks
6. Resource leaks — file/socket/db connections opened without `with` or explicit close
7. Error swallowing — `except: pass` or bare except catching all exceptions silently

Be conservative. A false negative (missing a real bug) is acceptable. A false positive (flagging safe code) wastes developer time and must be avoided.

Think step by step. Output ONLY valid JSON array, nothing else.
If no bugs found, output [].
"""


class BugDetectionAgent(BaseAgent):
    def system_prompt(self) -> str:
        return SYSTEM_PROMPT

    async def analyze(self, context: SharedContext) -> AgentResult:
        await self.emitter.agent_started()
        findings: list[Finding] = []

        files_text = "\n\n".join(
            f"=== {name} ===\n{self._number_lines(content)}"
            for name, content in context.files.items()
        )

        await self.emitter.thinking("Starting bug detection analysis...")

        try:
            raw = await self._run_with_streaming(context, files_text)
            findings = self._parse_findings(raw, context)
        except Exception as exc:
            await self.emitter.error(f"Bug detection agent error: {exc}")

        for f in findings:
            await self.emitter.finding(f.to_dict())

        await self.agent_drive.write_findings(context.session_id, self.agent_id, findings)

        await self.emitter.agent_completed(len(findings))
        return AgentResult(agent_id=self.agent_id, findings=findings)

    async def _run_with_streaming(self, context: SharedContext, files_text: str) -> str:
        from openai import AsyncOpenAI
        client = AsyncOpenAI()

        await self.emitter.thinking("Checking for null/None dereferences and logic errors...")
        await self.emitter.tool_call_start("openai.chat", {"model": "gpt-4o", "focus": "bugs"})

        full_response = ""
        stream = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Analyze these Python files for bugs and logic errors:\n\n{files_text}"},
            ],
            stream=True,
            temperature=0,
        )

        chunk_buffer = ""
        async for chunk in stream:
            delta = chunk.choices[0].delta.content or ""
            full_response += delta
            chunk_buffer += delta
            if len(chunk_buffer) > 80:
                await self.emitter.thinking(chunk_buffer.strip())
                chunk_buffer = ""

        if chunk_buffer.strip():
            await self.emitter.thinking(chunk_buffer.strip())

        await self.emitter.tool_call_result("openai.chat", f"{len(full_response)} chars", 0)
        return full_response

    def _parse_findings(self, raw: str, context: SharedContext) -> list[Finding]:
        findings = []
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if not match:
            return findings
        try:
            items = json.loads(match.group())
        except json.JSONDecodeError:
            return findings

        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                findings.append(Finding(
                    finding_id=self._make_finding_id(),
                    file=item.get("file", list(context.files.keys())[0]),
                    line=int(item.get("line", 0)),
                    severity=Severity(item.get("severity", "medium")),
                    category=item.get("category", "other"),
                    description=item.get("description", ""),
                    agent_id=self.agent_id,
                    code_snippet=item.get("code_snippet", ""),
                ))
            except Exception:
                continue
        return findings

    def _number_lines(self, code: str) -> str:
        lines = code.splitlines()
        return "\n".join(f"{i+1:3d}  {line}" for i, line in enumerate(lines))
