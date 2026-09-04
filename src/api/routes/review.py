from __future__ import annotations

import os
import shlex
import subprocess

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from ...agents.coordinator import CoordinatorAgent
from ...events.bus import AsyncEventBus
from ...events.emitter import EventEmitter
from ...review.session import ReviewSession, SessionStatus
from ...sandbox.manager import SandboxManager
from ...storage.agent_drive import AgentDrive
from ..dependencies import get_agent_drive, get_event_bus, get_sandbox_manager

router = APIRouter(prefix="/api", tags=["review"])

# In-memory session store (replace with Redis for production)
_sessions: dict[str, ReviewSession] = {}
_terminal_cwds: dict[str, str] = {}

MAX_UPLOAD_FILES = 20
MAX_UPLOAD_BYTES = 1024 * 1024  # 1 MB per file

# Uploading a folder sweeps in build artefacts and vendored code. Reviewing
# them wastes the file budget, floods the coordinator's prompt, and produces
# findings about code the user did not write.
_IGNORED_PATH_PARTS = frozenset({
    "__pycache__", ".git", ".venv", "venv", "node_modules", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "dist", "build", ".tox", "site-packages",
    ".idea", ".vscode", "htmlcov", ".eggs",
})
_IGNORED_SUFFIXES = (".pyc", ".pyo", ".pyd", ".so", ".egg-info", ".map", ".lock")


def is_reviewable(filename: str) -> bool:
    """Whether an uploaded path is source worth reviewing."""
    if not filename:
        return False
    normalised = filename.replace("\\", "/")
    parts = normalised.split("/")
    if any(part in _IGNORED_PATH_PARTS for part in parts):
        return False
    name = parts[-1]
    if name.startswith("."):
        return False
    return not name.endswith(_IGNORED_SUFFIXES)


class ReviewRequest(BaseModel):
    files: dict[str, str]  # filename -> source code
    session_id: str | None = None


class ReviewResponse(BaseModel):
    session_id: str
    status: str
    file_count: int


class TerminalRequest(BaseModel):
    session_id: str
    command: str


class TerminalResponse(BaseModel):
    cwd: str
    stdout: str
    stderr: str
    exit_code: int


@router.post("/review", response_model=ReviewResponse)
async def submit_review(
    body: ReviewRequest,
    background_tasks: BackgroundTasks,
    event_bus: AsyncEventBus = Depends(get_event_bus),
    sandbox_manager: SandboxManager = Depends(get_sandbox_manager),
    agent_drive: AgentDrive = Depends(get_agent_drive),
) -> ReviewResponse:
    session = ReviewSession.create(body.files)
    _sessions[session.session_id] = session
    background_tasks.add_task(
        _run_pipeline, session, event_bus, sandbox_manager, agent_drive
    )
    return ReviewResponse(
        session_id=session.session_id,
        status="started",
        file_count=len(body.files),
    )


@router.post("/review/upload", response_model=ReviewResponse)
async def upload_review(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    event_bus: AsyncEventBus = Depends(get_event_bus),
    sandbox_manager: SandboxManager = Depends(get_sandbox_manager),
    agent_drive: AgentDrive = Depends(get_agent_drive),
) -> ReviewResponse:
    reviewable = [f for f in files if is_reviewable(f.filename or "")]
    if not reviewable:
        raise HTTPException(
            status_code=400,
            detail="No reviewable source files in the upload.",
        )
    if len(reviewable) > MAX_UPLOAD_FILES:
        raise HTTPException(
            status_code=413,
            detail=f"Too many files: {len(reviewable)} > {MAX_UPLOAD_FILES}",
        )
    file_contents: dict[str, str] = {}
    for upload in reviewable:
        content = await upload.read()
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"File too large: {upload.filename} exceeds {MAX_UPLOAD_BYTES // 1024} KB",
            )
        file_contents[upload.filename or "unnamed.py"] = content.decode("utf-8", errors="replace")

    session = ReviewSession.create(file_contents)
    _sessions[session.session_id] = session
    background_tasks.add_task(
        _run_pipeline, session, event_bus, sandbox_manager, agent_drive
    )
    return ReviewResponse(
        session_id=session.session_id,
        status="started",
        file_count=len(file_contents),
    )


@router.get("/review/{session_id}")
async def get_session(session_id: str) -> dict:
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session_id": session.session_id,
        "status": session.status,
        "error": session.error,
        "result": session.result.model_dump() if session.result else None,
    }


@router.post("/terminal/exec", response_model=TerminalResponse)
async def execute_terminal_command(body: TerminalRequest) -> TerminalResponse:
    # Host-shell execution is opt-in: this endpoint runs commands on the
    # machine hosting the API, so it stays disabled unless explicitly enabled
    # for local development (RODEX_ENABLE_TERMINAL=1).
    if os.getenv("RODEX_ENABLE_TERMINAL") != "1":
        raise HTTPException(
            status_code=403,
            detail="Terminal is disabled. Set RODEX_ENABLE_TERMINAL=1 to enable it for local development.",
        )
    command = body.command.strip()
    cwd = _terminal_cwds.get(body.session_id, os.getcwd())

    if not command:
        return TerminalResponse(cwd=cwd, stdout="", stderr="", exit_code=0)

    if command == "clear":
        return TerminalResponse(cwd=cwd, stdout="__CLEAR__", stderr="", exit_code=0)

    if command.startswith("cd"):
        parts = shlex.split(command)
        target = parts[1] if len(parts) > 1 else os.path.expanduser("~")
        target_path = os.path.expanduser(target)
        if not os.path.isabs(target_path):
            target_path = os.path.join(cwd, target_path)
        next_cwd = os.path.abspath(target_path)
        if not os.path.isdir(next_cwd):
            return TerminalResponse(
                cwd=cwd,
                stdout="",
                stderr=f"cd: no such directory: {target}",
                exit_code=1,
            )
        _terminal_cwds[body.session_id] = next_cwd
        return TerminalResponse(cwd=next_cwd, stdout="", stderr="", exit_code=0)

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        _terminal_cwds[body.session_id] = cwd
        return TerminalResponse(
            cwd=cwd,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.returncode,
        )
    except subprocess.TimeoutExpired:
        return TerminalResponse(
            cwd=cwd,
            stdout="",
            stderr="Command timed out after 30s",
            exit_code=124,
        )


async def _run_pipeline(
    session: ReviewSession,
    event_bus: AsyncEventBus,
    sandbox_manager: SandboxManager,
    agent_drive: AgentDrive,
) -> None:
    session.status = SessionStatus.RUNNING
    emitter = EventEmitter("coordinator", session.session_id, event_bus)

    coordinator = CoordinatorAgent(
        emitter=emitter,
        sandbox_manager=sandbox_manager,
        agent_drive=agent_drive,
        session_id=session.session_id,
    )

    try:
        result = await coordinator.run_review(session.context, event_bus)
        session.result = result
        session.status = SessionStatus.COMPLETED
    except Exception as exc:
        session.error = str(exc)
        session.status = SessionStatus.FAILED
        await emitter.error(f"Pipeline failed: {exc}")
    finally:
        await sandbox_manager.destroy_session(session.session_id)
        await event_bus.close_session(session.session_id)
