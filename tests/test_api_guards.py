"""Tests for the API safety gates: terminal opt-in and upload limits."""

import importlib

from fastapi.testclient import TestClient


def _client(monkeypatch, enable_terminal: bool):
    if enable_terminal:
        monkeypatch.setenv("RODEX_ENABLE_TERMINAL", "1")
    else:
        monkeypatch.delenv("RODEX_ENABLE_TERMINAL", raising=False)
    from src.api.routes import review as review_module
    importlib.reload(review_module)
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(review_module.router)
    return TestClient(app), review_module


def test_terminal_disabled_by_default(monkeypatch):
    client, _ = _client(monkeypatch, enable_terminal=False)
    resp = client.post(
        "/api/terminal/exec",
        json={"session_id": "s1", "command": "echo hi"},
    )
    assert resp.status_code == 403
    assert "disabled" in resp.json()["detail"].lower()


def test_terminal_enabled_via_env(monkeypatch):
    client, _ = _client(monkeypatch, enable_terminal=True)
    resp = client.post(
        "/api/terminal/exec",
        json={"session_id": "s1", "command": ""},
    )
    assert resp.status_code == 200
    assert resp.json()["exit_code"] == 0


def test_upload_rejects_too_many_files(monkeypatch):
    client, review_module = _client(monkeypatch, enable_terminal=False)
    files = [
        ("files", (f"f{i}.py", b"print(1)", "text/x-python"))
        for i in range(review_module.MAX_UPLOAD_FILES + 1)
    ]
    resp = client.post("/api/review/upload", files=files)
    assert resp.status_code == 413


def test_upload_rejects_oversized_file(monkeypatch):
    client, review_module = _client(monkeypatch, enable_terminal=False)
    big = b"#" * (review_module.MAX_UPLOAD_BYTES + 1)
    resp = client.post(
        "/api/review/upload",
        files=[("files", ("big.py", big, "text/x-python"))],
    )
    assert resp.status_code == 413
