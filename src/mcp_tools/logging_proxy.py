"""Transparent proxy that logs an MCP server's tool invocations.

The agents hand MCP server objects to the Agents SDK, which then calls
tools on them directly. Nothing in that path passes through our code, so
MCP tool use was absent from the tool log even though it is the richest
signal about what a specialist actually did.

This wraps a server so every ``call_tool`` emits the same start/result
pair as any other tool, while every other attribute is delegated to the
real server untouched. Logging never changes the outcome of a call: a
failing tool still raises exactly what it raised before, and an emitter
error is swallowed rather than breaking the review.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_MAX_OUTPUT_CHARS = 300


def _summarize(result: Any) -> str:
    """Short, readable description of an MCP tool result."""
    content = getattr(result, "content", None)
    if content is None:
        return str(result)[:_MAX_OUTPUT_CHARS]

    texts: list[str] = []
    for item in content:
        text = getattr(item, "text", None)
        if text:
            texts.append(text)
        else:
            texts.append(f"<{getattr(item, 'type', 'content')}>")

    joined = " ".join(texts).strip()
    if getattr(result, "is_error", False):
        return f"error: {joined[:_MAX_OUTPUT_CHARS]}"
    if not joined:
        return "(no content)"
    if len(joined) > _MAX_OUTPUT_CHARS:
        return f"{joined[:_MAX_OUTPUT_CHARS]}… ({len(joined)} chars)"
    return joined


class LoggingMCPServer:
    """Wraps an MCP server so its tool calls appear in the event log."""

    def __init__(self, server: Any, emitter: Any, server_name: str = "") -> None:
        self._server = server
        self._emitter = emitter
        self._server_name = server_name or getattr(server, "name", "mcp")

    # The SDK reads `name` directly, so expose it as a plain attribute.
    @property
    def name(self) -> str:
        return self._server.name

    def __getattr__(self, item: str) -> Any:
        """Delegate everything we do not explicitly override."""
        return getattr(self._server, item)

    async def _emit_start(self, tool: str, arguments: Any) -> None:
        try:
            await self._emitter.tool_call_start(tool, {"arguments": arguments or {}})
        except Exception:  # noqa: BLE001 - logging must never break a review
            logger.debug("MCP tool_call_start emit failed", exc_info=True)

    async def _emit_result(self, tool: str, output: str, duration_ms: int) -> None:
        try:
            await self._emitter.tool_call_result(tool, output, duration_ms)
        except Exception:  # noqa: BLE001
            logger.debug("MCP tool_call_result emit failed", exc_info=True)

    async def call_tool(self, tool_name: str, arguments: Any = None, *args: Any, **kwargs: Any):
        """Invoke a tool on the wrapped server, logging both ends."""
        label = f"mcp.{self._server_name}.{tool_name}"
        start = time.monotonic()
        await self._emit_start(label, arguments)
        try:
            result = await self._server.call_tool(tool_name, arguments, *args, **kwargs)
        except Exception as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            await self._emit_result(label, f"error: {exc}", duration_ms)
            raise
        duration_ms = int((time.monotonic() - start) * 1000)
        await self._emit_result(label, _summarize(result), duration_ms)
        return result
