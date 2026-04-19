from __future__ import annotations

import json
import re
from typing import Any

from .base_agent import BaseAgent, AgentResult, SharedContext
from ..events.schemas import Finding, Severity

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
        from openai import AsyncOpenAI
        client = AsyncOpenAI()

        await self.emitter.thinking("Scanning for SQL injection patterns...")
        await self.emitter.tool_call_start("openai.chat", {"model": "gpt-4o", "focus": "security"})

        full_response = ""
        stream = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Analyze these Python files for security vulnerabilities:\n\n{files_text}"},
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

        import time
        await self.emitter.tool_call_result("openai.chat", f"{len(full_response)} chars", 0)
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
