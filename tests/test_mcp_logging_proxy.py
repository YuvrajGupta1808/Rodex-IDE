"""Tests for the MCP tool-call logging proxy.

MCP tools are invoked by the Agents SDK directly on the server object, so
they bypassed our code entirely and never reached the tool log. The proxy
intercepts call_tool while staying otherwise transparent — and must never
change the outcome of a call.
"""

import pytest

from src.mcp_tools.logging_proxy import LoggingMCPServer


class _Text:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Result:
    def __init__(self, texts, is_error=False):
        self.content = [_Text(t) for t in texts]
        self.is_error = is_error


class _Server:
    name = "git"

    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error
        self.calls = []
        self.cleaned = False

    async def call_tool(self, tool_name, arguments=None, *args, **kwargs):
        self.calls.append((tool_name, arguments))
        if self._error:
            raise self._error
        return self._result

    async def cleanup(self):
        self.cleaned = True

    async def list_tools(self, *args, **kwargs):
        return ["git_log", "git_diff"]


class _Emitter:
    def __init__(self, fail=False):
        self.starts = []
        self.results = []
        self._fail = fail

    async def tool_call_start(self, tool_name, inputs):
        if self._fail:
            raise RuntimeError("bus down")
        self.starts.append((tool_name, inputs))

    async def tool_call_result(self, tool_name, output, duration_ms):
        if self._fail:
            raise RuntimeError("bus down")
        self.results.append((tool_name, output, duration_ms))


@pytest.mark.asyncio
async def test_tool_call_is_logged_with_server_qualified_name():
    server = _Server(_Result(["commit abc123"]))
    emitter = _Emitter()
    proxy = LoggingMCPServer(server, emitter)

    result = await proxy.call_tool("git_log", {"n": 1})

    assert result is server._result  # the real result is passed through
    assert emitter.starts == [("mcp.git.git_log", {"arguments": {"n": 1}})]
    name, output, _ = emitter.results[0]
    assert name == "mcp.git.git_log"
    assert output == "commit abc123"


@pytest.mark.asyncio
async def test_arguments_reach_the_wrapped_server_unchanged():
    server = _Server(_Result(["ok"]))
    proxy = LoggingMCPServer(server, _Emitter())

    await proxy.call_tool("git_diff", {"path": "a.py"})

    assert server.calls == [("git_diff", {"path": "a.py"})]


@pytest.mark.asyncio
async def test_error_results_are_marked():
    proxy = LoggingMCPServer(_Server(_Result(["no such ref"], is_error=True)), (e := _Emitter()))

    await proxy.call_tool("git_log", {})

    assert e.results[0][1].startswith("error: ")


@pytest.mark.asyncio
async def test_raised_exception_is_logged_and_re_raised():
    emitter = _Emitter()
    proxy = LoggingMCPServer(_Server(error=RuntimeError("server died")), emitter)

    with pytest.raises(RuntimeError, match="server died"):
        await proxy.call_tool("git_log", {})

    # The failure is visible in the log rather than vanishing.
    assert "error: server died" in emitter.results[0][1]


@pytest.mark.asyncio
async def test_emitter_failure_never_breaks_the_tool_call():
    server = _Server(_Result(["still works"]))
    proxy = LoggingMCPServer(server, _Emitter(fail=True))

    result = await proxy.call_tool("git_log", {})

    assert result is server._result


@pytest.mark.asyncio
async def test_long_output_is_truncated_with_a_length_hint():
    proxy = LoggingMCPServer(_Server(_Result(["x" * 500])), (e := _Emitter()))

    await proxy.call_tool("git_log", {})

    output = e.results[0][1]
    assert output.endswith("(500 chars)")
    assert len(output) < 500


@pytest.mark.asyncio
async def test_other_attributes_are_delegated():
    server = _Server(_Result(["ok"]))
    proxy = LoggingMCPServer(server, _Emitter())

    assert proxy.name == "git"
    assert await proxy.list_tools() == ["git_log", "git_diff"]

    await proxy.cleanup()
    assert server.cleaned is True
