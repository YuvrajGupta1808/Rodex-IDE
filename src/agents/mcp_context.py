"""Gather MCP context for a review before the model is asked to analyse.

The specialists stream their analysis in a single completion, so they
cannot pause mid-stream to call a tool. Rather than rebuild them around a
multi-turn tool loop, this collects context up front from read-only MCP
tools and prepends it to the prompt.

That keeps the streaming path — and its live reasoning — intact while
still grounding the review in repository context, and every tool call
goes through the logging proxy so it appears in the tool log.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Read-only tools worth pulling in before a review, with their arguments.
# Anything not listed here is never called automatically.
_CONTEXT_TOOLS: dict[str, dict[str, Any]] = {
    "git_status": {"repo_path": "."},
    "git_diff_unstaged": {"repo_path": "."},
}

_MAX_CHARS_PER_TOOL = 2000


def _text_of(result: Any) -> str:
    content = getattr(result, "content", None)
    if not content:
        return ""
    parts = [getattr(item, "text", "") or "" for item in content]
    return "\n".join(p for p in parts if p).strip()


async def gather(servers: list[Any], emitter: Any = None) -> str:
    """Context block from the given MCP servers, or "" when unavailable.

    Failures are logged and skipped: MCP must never break a review.
    """
    if not servers:
        return ""

    sections: list[str] = []
    for server in servers:
        try:
            tools = await server.list_tools()
        except Exception:  # noqa: BLE001 - a dead server must not stop the review
            logger.debug("MCP list_tools failed", exc_info=True)
            continue

        available = {getattr(t, "name", str(t)) for t in tools}
        for name, arguments in _CONTEXT_TOOLS.items():
            if name not in available:
                continue
            try:
                result = await server.call_tool(name, arguments)
            except Exception:  # noqa: BLE001
                logger.debug("MCP tool %r failed", name, exc_info=True)
                continue
            text = _text_of(result)
            if text:
                sections.append(f"### {name}\n{text[:_MAX_CHARS_PER_TOOL]}")

    if not sections:
        return ""

    if emitter is not None:
        try:
            await emitter.thinking(
                f"Gathered repository context from {len(sections)} MCP tool(s)."
            )
        except Exception:  # noqa: BLE001
            pass

    body = "\n\n".join(sections)
    return (
        "Repository context (from MCP tools; use it to judge whether a "
        "finding is real and current):\n\n" + body + "\n\n"
    )
