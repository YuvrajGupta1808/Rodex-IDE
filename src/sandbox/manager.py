from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from ..events.emitter import EventEmitter


class ProcessResult:
    def __init__(self, stdout: str, stderr: str, exit_code: int) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.success = exit_code == 0


class SandboxManager:
    """
    Manages Blaxel sandbox lifecycle using the official blaxel SDK.
    Falls back to _MockSandbox for local development.
    """

    def __init__(self, workspace: str, api_key: str) -> None:
        self.workspace = workspace
        self.api_key = api_key
        self._sandboxes: dict[str, Any] = {}
        # Configure blaxel SDK credentials
        if api_key:
            self._configure_blaxel()

    def _configure_blaxel(self) -> None:
        try:
            from blaxel.core.common.settings import settings
            settings.api_key = self.api_key
            if self.workspace:
                settings.workspace = self.workspace
        except Exception:
            pass

    async def get_or_create_sandbox(self, session_id: str, agent_id: str) -> Any:
        key = f"{session_id}:{agent_id}"
        if key in self._sandboxes:
            return self._sandboxes[key]
        sandbox = await self._create_sandbox(session_id, agent_id)
        self._sandboxes[key] = sandbox
        return sandbox

    async def _create_sandbox(self, session_id: str, agent_id: str) -> Any:
        name = f"review-{session_id[:8]}-{agent_id}"
        try:
            from blaxel.core.sandbox.default.sandbox import SandboxInstance
            sandbox = await SandboxInstance.create({
                "name": name,
                "image": "blaxel/py-app:latest",
                "memory": 2048,
                "region": os.getenv("BL_REGION", "us-pdx-1"),
                "ttl": "1h",
            }, safe=True)
            return sandbox
        except Exception as exc:
            return _MockSandbox(name)

    async def write_file(self, sandbox: Any, path: str, content: str) -> None:
        try:
            if hasattr(sandbox, "fs"):
                await sandbox.fs.write(path, content)
            else:
                await sandbox.filesystem.writeFile(path, content)
        except Exception:
            pass

    async def read_file(self, sandbox: Any, path: str) -> str:
        try:
            if hasattr(sandbox, "fs"):
                result = await sandbox.fs.read(path)
                return result if isinstance(result, str) else result.content or ""
            return await sandbox.filesystem.readFile(path)
        except Exception:
            return ""

    async def exec_with_streaming(
        self,
        sandbox: Any,
        command: str,
        emitter: EventEmitter,
        tool_name: str,
    ) -> ProcessResult:
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        start = time.monotonic()

        await emitter.tool_call_start(tool_name, {"command": command})

        try:
            if hasattr(sandbox, "process"):
                result = await sandbox.process.exec({
                    "command": command,
                    "wait_for_completion": True,
                    "timeout": 30,
                    "on_log": lambda line: asyncio.create_task(
                        emitter.thinking(f"[{tool_name}] {line}")
                    ),
                })
                # Extract output from ProcessResponse
                if hasattr(result, "logs") and result.logs:
                    for log in result.logs:
                        if hasattr(log, "message"):
                            stdout_lines.append(log.message)
                # Check exit code
                exit_code = 0
                if hasattr(result, "exit_code") and result.exit_code is not None:
                    exit_code = result.exit_code
                elif hasattr(result, "status"):
                    exit_code = 0 if str(result.status).lower() in ("completed", "exited") else 1
            else:
                exit_code = 1
                stderr_lines.append("Sandbox process API not available")

        except Exception as exc:
            stderr_lines.append(str(exc))
            exit_code = 1

        duration_ms = int((time.monotonic() - start) * 1000)
        stdout = "\n".join(stdout_lines)
        stderr = "\n".join(stderr_lines)

        await emitter.tool_call_result(
            tool_name,
            {"stdout": stdout[:1000], "stderr": stderr[:500]},
            duration_ms,
        )
        return ProcessResult(stdout=stdout, stderr=stderr, exit_code=exit_code)

    async def exec_simple(self, sandbox: Any, command: str) -> ProcessResult:
        """Execute without streaming — for quick syntax checks."""
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        try:
            if hasattr(sandbox, "process"):
                result = await sandbox.process.exec({
                    "command": command,
                    "wait_for_completion": True,
                    "timeout": 15,
                })
                if hasattr(result, "logs") and result.logs:
                    for log in result.logs:
                        if hasattr(log, "message"):
                            stdout_lines.append(log.message)
                exit_code = 0
                if hasattr(result, "exit_code") and result.exit_code is not None:
                    exit_code = result.exit_code
                elif hasattr(result, "status"):
                    exit_code = 0 if str(result.status).lower() in ("completed", "exited") else 1
            else:
                exit_code = 1
        except Exception as exc:
            stderr_lines.append(str(exc))
            exit_code = 1

        return ProcessResult(
            stdout="\n".join(stdout_lines),
            stderr="\n".join(stderr_lines),
            exit_code=exit_code,
        )

    async def destroy_session(self, session_id: str) -> None:
        keys = [k for k in self._sandboxes if k.startswith(f"{session_id}:")]
        for key in keys:
            sandbox = self._sandboxes.pop(key)
            if not isinstance(sandbox, _MockSandbox):
                try:
                    if hasattr(sandbox, "delete"):
                        await sandbox.delete()
                except Exception:
                    pass


class _MockSandbox:
    """Fallback for local development without Blaxel credentials."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._files: dict[str, str] = {}
        self.process = _MockProcess(self._files)
        self.fs = _MockFilesystem(self._files)

    async def delete(self) -> None:
        pass


class _MockProcess:
    def __init__(self, files: dict[str, str]) -> None:
        self._files = files

    async def exec(self, process_req: Any, **kwargs) -> Any:
        import subprocess
        if isinstance(process_req, dict):
            command = process_req.get("command", "")
        else:
            command = getattr(process_req, "command", "")

        on_log = None
        if isinstance(process_req, dict):
            on_log = process_req.get("on_log")

        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
        if on_log:
            for line in (result.stdout + result.stderr).splitlines():
                if line.strip():
                    try:
                        task = on_log(line)
                        if asyncio.iscoroutine(task):
                            asyncio.create_task(task)
                    except Exception:
                        pass

        exit_code = result.returncode
        return type("R", (), {
            "exit_code": exit_code,
            "status": "completed" if exit_code == 0 else "failed",
            "logs": [type("L", (), {"message": l})() for l in (result.stdout + result.stderr).splitlines()],
            "stdout": result.stdout,
            "stderr": result.stderr,
        })()


class _MockFilesystem:
    def __init__(self, files: dict[str, str]) -> None:
        self._files = files

    async def write(self, path: str, content: str) -> None:
        import os, pathlib
        self._files[path] = content
        # Also write to actual filesystem for local py_compile checks
        try:
            p = pathlib.Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        except Exception:
            pass

    async def read(self, path: str) -> str:
        if path in self._files:
            return self._files[path]
        try:
            import pathlib
            return pathlib.Path(path).read_text()
        except Exception:
            return ""
