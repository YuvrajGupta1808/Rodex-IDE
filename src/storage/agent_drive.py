from __future__ import annotations

import json
import os
from typing import Any

from ..events.schemas import Finding, FixProposal, FixVerification


class AgentDrive:
    """
    Manages the Blaxel Agent Drive — a shared POSIX filesystem mounted
    across all agent sandboxes simultaneously.

    Falls back to an in-memory dict when running without Blaxel credentials.
    """

    REGION = "us-was-1"

    def __init__(self, workspace: str = "", api_key: str = "") -> None:
        self._workspace = workspace
        self._api_key = api_key
        self._drive: Any = None
        # In-memory fallback for local dev
        self._mem: dict[str, str] = {}

    async def ensure_drive(self, name: str = "code-review-drive") -> None:
        try:
            from blaxel.drive import DriveInstance  # type: ignore
            self._drive = await DriveInstance.create_if_not_exists(
                name=name,
                region=self.REGION,
            )
        except (ImportError, Exception):
            self._drive = None

    async def write(self, path: str, content: str) -> None:
        if self._drive:
            try:
                await self._drive.write(path, content)
                return
            except Exception:
                pass
        self._mem[path] = content

    async def read(self, path: str) -> str:
        if self._drive:
            try:
                return await self._drive.read(path)
            except Exception:
                pass
        return self._mem.get(path, "")

    async def upload_sources(
        self, session_id: str, files: dict[str, str]
    ) -> None:
        for filename, content in files.items():
            path = f"/review/{session_id}/src/{filename}"
            await self.write(path, content)

    async def write_findings(
        self, session_id: str, agent_id: str, findings: list[Finding]
    ) -> None:
        path = f"/review/{session_id}/findings/{agent_id}.json"
        data = [f.model_dump() for f in findings]
        await self.write(path, json.dumps(data, default=str))

    async def read_findings(
        self, session_id: str, agent_id: str
    ) -> list[Finding]:
        path = f"/review/{session_id}/findings/{agent_id}.json"
        raw = await self.read(path)
        if not raw:
            return []
        try:
            data = json.loads(raw)
            return [Finding(**item) for item in data]
        except Exception:
            return []

    async def snapshot_source(self, session_id: str, filepath: str) -> None:
        """Save current file content before applying a fix — for rollback."""
        content = await self.read(f"/review/{session_id}/src/{filepath}")
        await self.write(f"/review/{session_id}/snapshots/{filepath}", content)

    async def restore_snapshot(self, session_id: str, filepath: str) -> None:
        content = await self.read(f"/review/{session_id}/snapshots/{filepath}")
        if content:
            await self.write(f"/review/{session_id}/src/{filepath}", content)

    async def read_source(self, session_id: str, filename: str) -> str:
        return await self.read(f"/review/{session_id}/src/{filename}")

    async def list_sources(self, session_id: str) -> list[str]:
        prefix = f"/review/{session_id}/src/"
        return [k[len(prefix):] for k in self._mem if k.startswith(prefix)]

    async def write_plan(self, session_id: str, plan: dict) -> None:
        await self.write(
            f"/review/{session_id}/plan.json", json.dumps(plan, default=str)
        )
