"""Sandbox file writes must appear in the tool log.

Only the LLM calls and py_compile were ever logged, so the sandbox
writes that make up most of the fix pipeline were invisible — and a
failed write was silently indistinguishable from a successful one.
"""

import pytest

from src.sandbox.manager import SandboxManager


class _Emitter:
    def __init__(self):
        self.starts = []
        self.results = []

    async def tool_call_start(self, tool_name, inputs):
        self.starts.append((tool_name, inputs))

    async def tool_call_result(self, tool_name, output, duration_ms):
        self.results.append((tool_name, output, duration_ms))


class _Filesystem:
    def __init__(self, fail=False):
        self.fail = fail
        self.written = {}

    async def writeFile(self, path, content):  # noqa: N802 - SDK's name
        if self.fail:
            raise RuntimeError("disk full")
        self.written[path] = content


class _Sandbox:
    def __init__(self, fail=False):
        self.filesystem = _Filesystem(fail)


@pytest.mark.asyncio
async def test_write_is_logged_with_path_and_size():
    emitter = _Emitter()
    sandbox = _Sandbox()

    await SandboxManager(workspace="", api_key="").write_file(sandbox, "/tmp/m.py", "x = 1", emitter=emitter)

    assert emitter.starts == [("sandbox.write_file", {"path": "/tmp/m.py", "bytes": 5})]
    tool, output, _ = emitter.results[0]
    assert tool == "sandbox.write_file"
    assert "wrote 5 bytes to /tmp/m.py" == output
    assert sandbox.filesystem.written["/tmp/m.py"] == "x = 1"


@pytest.mark.asyncio
async def test_failed_write_is_reported_not_swallowed():
    emitter = _Emitter()

    await SandboxManager(workspace="", api_key="").write_file(
        _Sandbox(fail=True), "/tmp/m.py", "x = 1", emitter=emitter
    )

    _, output, _ = emitter.results[0]
    assert "failed" in output
    assert "disk full" in output


@pytest.mark.asyncio
async def test_write_without_emitter_stays_silent():
    sandbox = _Sandbox()

    await SandboxManager(workspace="", api_key="").write_file(sandbox, "/tmp/m.py", "x = 1")

    assert sandbox.filesystem.written["/tmp/m.py"] == "x = 1"
