from __future__ import annotations

import re
import time
from typing import Any

from ..events.emitter import EventEmitter


class ApplyResult:
    def __init__(self, success: bool, diff: str = "", error: str = "") -> None:
        self.success = success
        self.diff = diff
        self.error = error


class CodegenTools:
    """
    Wraps Blaxel codegen MCP tools: fastapply, semantic search, grep, directory listing.
    Falls back to local implementations when Blaxel sandbox is unavailable.
    """

    def __init__(self, sandbox: Any, morph_api_key: str = "") -> None:
        self._sandbox = sandbox
        self._morph_api_key = morph_api_key

    async def fast_apply(
        self,
        filepath: str,
        instruction: str,
        emitter: EventEmitter,
    ) -> ApplyResult:
        start = time.monotonic()
        await emitter.tool_call_start(
            "codegen.fastapply",
            {"filepath": filepath, "instruction": instruction[:200]},
        )
        try:
            result = await self._sandbox.codegen.fastapply(filepath, instruction)
            duration_ms = int((time.monotonic() - start) * 1000)
            await emitter.tool_call_result("codegen.fastapply", result, duration_ms)
            return ApplyResult(success=True, diff=str(result))
        except AttributeError:
            # Sandbox doesn't have codegen — use direct file edit
            return await self._local_apply(filepath, instruction, emitter, start)
        except Exception as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            await emitter.tool_call_result("codegen.fastapply", str(exc), duration_ms)
            return ApplyResult(success=False, error=str(exc))

    async def _local_apply(
        self,
        filepath: str,
        instruction: str,
        emitter: EventEmitter,
        start: float,
    ) -> ApplyResult:
        """Local fallback: read file, apply LLM edit, write back."""
        try:
            content = await self._sandbox.filesystem.readFile(filepath)
            edited = await self._apply_instruction_locally(content, instruction, filepath)
            await self._sandbox.filesystem.writeFile(filepath, edited)
            duration_ms = int((time.monotonic() - start) * 1000)
            await emitter.tool_call_result("codegen.fastapply", "applied locally", duration_ms)
            return ApplyResult(success=True, diff=f"Applied: {instruction[:100]}")
        except Exception as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            await emitter.tool_call_result("codegen.fastapply", str(exc), duration_ms)
            return ApplyResult(success=False, error=str(exc))

    async def _apply_instruction_locally(
        self, content: str, instruction: str, filepath: str
    ) -> str:
        """Use OpenAI to apply the instruction to the file content."""
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI()
            response = await client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a code editor. Apply the given instruction to the code. "
                            "Return ONLY the complete modified file content, no explanations."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"File: {filepath}\n\nInstruction: {instruction}\n\nCode:\n{content}",
                    },
                ],
                temperature=0,
            )
            return response.choices[0].message.content or content
        except Exception:
            return content

    async def grep_search(
        self, pattern: str, path: str, emitter: EventEmitter
    ) -> list[dict]:
        start = time.monotonic()
        await emitter.tool_call_start("codegen.grep", {"pattern": pattern, "path": path})
        try:
            result = await self._sandbox.codegen.grep(pattern, path)
            duration_ms = int((time.monotonic() - start) * 1000)
            await emitter.tool_call_result("codegen.grep", result, duration_ms)
            return result if isinstance(result, list) else []
        except Exception:
            return []

    async def list_directory(self, path: str) -> list[str]:
        try:
            return await self._sandbox.codegen.listDir(path)
        except Exception:
            return []
