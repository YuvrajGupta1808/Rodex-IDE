from __future__ import annotations

import json
import re
import time

from ..events.schemas import Finding, Severity
from .base_agent import AgentResult, BaseAgent, SharedContext

SYSTEM_PROMPT = """You are a specialized security code reviewer for Python code.

Your job is to find REAL security vulnerabilities. For each issue found, output a JSON array of findings.

Each finding must be a JSON object with these exact fields:
{
  "file": "filename.py",
  "line": <line number as integer>,
  "severity": "critical" | "high" | "medium" | "low",
  "category": "sql_injection" | "xss" | "hardcoded_secret" | "auth_flaw" | "unsafe_deserialization" | "command_injection" | "path_traversal" | "other",
  "description": "Clear description of the vulnerability",
  "code_snippet": "The vulnerable line of code"
}

CRITICAL RULES — apply these before flagging anything:

Only flag issues that are actual security vulnerabilities, not general code quality or None-handling issues. In particular:
- Null/None dereference patterns (e.g., `x.attr if x else None`, `x and x.attr`) are NOT security vulnerabilities — do not flag them.
- Defensive patterns like `if x is not None: ...` or ternary guards are SAFE — do not flag.
- Only flag something if an attacker could exploit it to compromise the system (data exfiltration, RCE, auth bypass, etc.).

Focus on:
1. SQL injection — string concatenation or f-strings in queries with user-controlled data
2. XSS — unescaped user input rendered in HTML/templates
3. Hardcoded secrets — literal API keys, passwords, tokens in source
4. Authentication flaws — missing auth checks, timing-safe comparison violations
5. Unsafe deserialization — pickle.loads on untrusted data
6. Command injection — os.system/subprocess with user input and shell=True
7. Path traversal — unvalidated file paths constructed from user input

Be conservative. Only flag clear, exploitable vulnerabilities. Do not flag null checks, type checks, or general defensive coding patterns.

Think step by step. Output ONLY valid JSON array, nothing else.
If no vulnerabilities found, output [].
"""


class SecurityAgent(BaseAgent):
    def system_prompt(self) -> str:
        return SYSTEM_PROMPT

    async def analyze(self, context: SharedContext) -> AgentResult:
        await self.emitter.agent_started()
        findings: list[Finding] = []

        files_text = "\n\n".join(
            f"=== {name} ===\n{self._number_lines(content)}"
            for name, content in context.files.items()
        )

        await self.emitter.thinking("Starting security analysis across all files...")

        try:
            raw = await self._run_with_streaming(context, files_text)
            findings = self._parse_findings(raw, context)
        except Exception as exc:
            await self.emitter.error(f"Security agent error: {exc}")

        for f in findings:
            await self.emitter.finding(f.to_dict())

        # Write findings to Agent Drive for Coordinator to read
        await self.agent_drive.write_findings(context.session_id, self.agent_id, findings)

        await self.emitter.agent_completed(len(findings))
        return AgentResult(agent_id=self.agent_id, findings=findings)

    async def _run_with_streaming(self, context: SharedContext, files_text: str) -> str:
        from .llm_client import get_client, get_model, thinking_extra_body, tool_name
        from .mcp_context import gather as gather_mcp_context
        from .streaming import stream_thinking, summarize_response
        client = get_client()
        model = get_model()

        await self.emitter.thinking("Scanning for SQL injection patterns...")
        tool = tool_name()
        await self.emitter.tool_call_start(tool, {"model": model, "focus": "security"})

        mcp_context = await gather_mcp_context(await self._mcp_servers(), self.emitter)
        # The coordinator dispatches with a focus naming what it wants
        # scrutinised; honour it so delegation is more than decorative.
        focus = (context.metadata or {}).get("focus", "")
        focus_note = (
            f"The coordinator asked you to focus on: {focus}\n\n" if focus else ""
        )

        stream = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"{mcp_context}{focus_note}Analyze these Python files for security vulnerabilities:\n\n{files_text}"},
            ],
            stream=True,
            temperature=0,
            stream_options={"include_usage": True},
            extra_body=thinking_extra_body(),
        )

        started = time.monotonic()
        usage_holder: dict = {}

        def _record(usage):
            usage_holder["usage"] = usage
            self.telemetry.record_usage(
                self.agent_id, model, usage, int((time.monotonic() - started) * 1000)
            )

        full_response = await stream_thinking(
            stream,
            self.emitter,
            on_usage=_record,
        )

        duration_ms = int((time.monotonic() - started) * 1000)
        await self.emitter.tool_call_result(
            tool, summarize_response(full_response, usage_holder.get("usage")), duration_ms
        )
        return full_response

    def _parse_findings(self, raw: str, context: SharedContext) -> list[Finding]:
        findings = []
        # Extract JSON array from response
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
