from __future__ import annotations

import os
from functools import lru_cache

from ..events.bus import AsyncEventBus
from ..sandbox.manager import SandboxManager
from ..storage.agent_drive import AgentDrive
from ..storage.volume import VolumeStore

_event_bus: AsyncEventBus | None = None
_sandbox_manager: SandboxManager | None = None
_agent_drive: AgentDrive | None = None
_volume_store: VolumeStore | None = None


def get_event_bus() -> AsyncEventBus:
    global _event_bus
    if _event_bus is None:
        _event_bus = AsyncEventBus()
    return _event_bus


def get_sandbox_manager() -> SandboxManager:
    global _sandbox_manager
    if _sandbox_manager is None:
        _sandbox_manager = SandboxManager(
            workspace=os.getenv("BL_WORKSPACE", ""),
            api_key=os.getenv("BL_API_KEY", ""),
        )
    return _sandbox_manager


def get_agent_drive() -> AgentDrive:
    global _agent_drive
    if _agent_drive is None:
        _agent_drive = AgentDrive(
            workspace=os.getenv("BL_WORKSPACE", ""),
            api_key=os.getenv("BL_API_KEY", ""),
        )
    return _agent_drive


def get_volume_store() -> VolumeStore:
    global _volume_store
    if _volume_store is None:
        _volume_store = VolumeStore()
    return _volume_store
