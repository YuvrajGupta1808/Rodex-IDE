"""MCP (Model Context Protocol) server integration for review agents.

Each specialist agent can be granted tools from external MCP servers —
filesystem access, git history, dependency lookups — so its review of a
specific section of code is grounded in richer context than the raw file
text alone.

Servers are declared in ``mcp_servers.json`` at the repo root (see
``mcp_servers.example.json``). Each entry maps a server name to a stdio
launch command plus an optional ``agents`` allowlist restricting which
specialist agents may use that server's tools. With no config file
present, everything degrades gracefully to the previous no-MCP behavior.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CONFIG_FILENAME = "mcp_servers.json"


@dataclass
class MCPServerSpec:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    # Which agent ids may use this server's tools; empty = all agents.
    agents: list[str] = field(default_factory=list)

    def allowed_for(self, agent_id: str) -> bool:
        return not self.agents or agent_id in self.agents


def load_mcp_config(root: Path | None = None) -> list[MCPServerSpec]:
    """Read mcp_servers.json from the repo root. Missing file -> []."""
    root = root or Path(__file__).resolve().parents[2]
    path = root / CONFIG_FILENAME
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Ignoring unreadable %s: %s", CONFIG_FILENAME, exc)
        return []
    specs: list[MCPServerSpec] = []
    for name, entry in raw.get("servers", {}).items():
        if not isinstance(entry, dict) or "command" not in entry:
            logger.warning("Skipping malformed MCP server entry %r", name)
            continue
        specs.append(
            MCPServerSpec(
                name=name,
                command=entry["command"],
                args=list(entry.get("args", [])),
                env=dict(entry.get("env", {})),
                agents=list(entry.get("agents", [])),
            )
        )
    return specs


class MCPServerManager:
    """Owns the lifecycle of MCP server connections for one review session.

    Usage::

        manager = MCPServerManager()
        servers = await manager.servers_for("security")   # connected servers
        ...
        await manager.aclose()
    """

    def __init__(self, specs: list[MCPServerSpec] | None = None) -> None:
        self._specs = specs if specs is not None else load_mcp_config()
        self._connected: dict[str, Any] = {}

    @property
    def configured(self) -> bool:
        return bool(self._specs)

    async def servers_for(self, agent_id: str) -> list[Any]:
        """Return connected MCP server objects this agent may use.

        Servers are connected lazily on first request and shared across
        agents for the rest of the session. Connection failures are logged
        and skipped so a dead server never blocks a review.
        """
        result: list[Any] = []
        for spec in self._specs:
            if not spec.allowed_for(agent_id):
                continue
            server = await self._connect(spec)
            if server is not None:
                result.append(server)
        return result

    async def _connect(self, spec: MCPServerSpec) -> Any | None:
        if spec.name in self._connected:
            return self._connected[spec.name]
        try:
            from agents.mcp import MCPServerStdio  # type: ignore

            server = MCPServerStdio(
                name=spec.name,
                params={
                    "command": spec.command,
                    "args": spec.args,
                    "env": spec.env or None,
                },
                cache_tools_list=True,
            )
            await server.connect()
        except Exception as exc:  # noqa: BLE001 - any startup failure is non-fatal
            logger.warning("MCP server %r unavailable: %s", spec.name, exc)
            self._connected[spec.name] = None
            return None
        self._connected[spec.name] = server
        return server

    async def aclose(self) -> None:
        for name, server in self._connected.items():
            if server is None:
                continue
            try:
                await server.cleanup()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Error closing MCP server %r: %s", name, exc)
        self._connected.clear()
