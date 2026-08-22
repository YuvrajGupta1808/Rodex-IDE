import asyncio
import json
from pathlib import Path

from src.mcp_tools.manager import MCPServerManager, MCPServerSpec, load_mcp_config


def test_load_missing_config_returns_empty(tmp_path: Path):
    assert load_mcp_config(tmp_path) == []


def test_load_config_parses_servers_and_allowlists(tmp_path: Path):
    (tmp_path / "mcp_servers.json").write_text(json.dumps({
        "servers": {
            "git": {"command": "uvx", "args": ["mcp-server-git"], "agents": ["security"]},
            "fs": {"command": "npx", "args": ["-y", "server-filesystem"]},
            "bad": {"args": ["missing-command"]},
        }
    }))
    specs = load_mcp_config(tmp_path)
    assert [s.name for s in specs] == ["git", "fs"]
    git, fs = specs
    assert git.allowed_for("security") and not git.allowed_for("fix")
    assert fs.allowed_for("security") and fs.allowed_for("fix")


def test_malformed_json_is_ignored(tmp_path: Path):
    (tmp_path / "mcp_servers.json").write_text("{not json")
    assert load_mcp_config(tmp_path) == []


def test_manager_unconfigured_yields_no_servers():
    manager = MCPServerManager(specs=[])
    assert not manager.configured
    assert asyncio.run(manager.servers_for("security")) == []


def test_manager_skips_unavailable_servers():
    spec = MCPServerSpec(name="ghost", command="definitely-not-a-real-binary")
    manager = MCPServerManager(specs=[spec])
    assert manager.configured
    # Connection fails -> server skipped, never raises.
    assert asyncio.run(manager.servers_for("security")) == []
    asyncio.run(manager.aclose())
