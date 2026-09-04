"""Tests for pre-review MCP context gathering."""

import pytest

from src.agents.mcp_context import gather


class _Text:
    def __init__(self, text):
        self.text = text


class _Result:
    def __init__(self, text):
        self.content = [_Text(text)]
        self.is_error = False


class _Tool:
    def __init__(self, name):
        self.name = name


class _Server:
    def __init__(self, tools, results=None, list_error=None, call_error=None):
        self._tools = [_Tool(t) for t in tools]
        self._results = results or {}
        self._list_error = list_error
        self._call_error = call_error
        self.called = []

    async def list_tools(self):
        if self._list_error:
            raise self._list_error
        return self._tools

    async def call_tool(self, name, arguments=None):
        self.called.append(name)
        if self._call_error:
            raise self._call_error
        return _Result(self._results.get(name, "output"))


@pytest.mark.asyncio
async def test_no_servers_yields_no_context():
    assert await gather([]) == ""


@pytest.mark.asyncio
async def test_gathers_only_known_read_only_tools():
    server = _Server(["git_status", "git_commit", "git_diff_unstaged"])

    context = await gather([server])

    # git_commit writes, so it is never called automatically.
    assert "git_commit" not in server.called
    assert set(server.called) == {"git_status", "git_diff_unstaged"}
    assert "Repository context" in context


@pytest.mark.asyncio
async def test_tool_output_is_included():
    server = _Server(["git_status"], {"git_status": "On branch main"})

    context = await gather([server])

    assert "### git_status" in context
    assert "On branch main" in context


@pytest.mark.asyncio
async def test_a_failing_server_is_skipped_not_fatal():
    broken = _Server(["git_status"], list_error=RuntimeError("dead"))
    working = _Server(["git_status"], {"git_status": "clean"})

    context = await gather([broken, working])

    assert "clean" in context


@pytest.mark.asyncio
async def test_a_failing_tool_call_is_skipped():
    server = _Server(["git_status"], call_error=RuntimeError("timeout"))

    assert await gather([server]) == ""


@pytest.mark.asyncio
async def test_server_without_matching_tools_contributes_nothing():
    assert await gather([_Server(["unrelated_tool"])]) == ""
