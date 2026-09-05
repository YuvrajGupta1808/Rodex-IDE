from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .auth import (
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE_SECONDS,
    SessionAuthMiddleware,
    create_session_cookie,
)
from .dependencies import get_agent_drive, get_volume_store
from .routes.review import router as review_router
from .routes.stream import router as stream_router

load_dotenv()

UI_DIR = Path(__file__).parent.parent.parent / "ui"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize Blaxel storage on startup
    drive = get_agent_drive()
    await drive.ensure_drive()
    volume = get_volume_store()
    await volume.ensure_volumes()
    yield


app = FastAPI(
    title="Multi-Agent Code Review IDE",
    description="AI-powered code review with real-time streaming",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS: default to local dev origins only. A wildcard here would let any
# website script against this API from the user's browser (CSRF), which is
# unacceptable for endpoints that touch the filesystem or run commands.
# Override with RODEX_ALLOWED_ORIGINS (comma-separated) when deploying.
_default_origins = "http://localhost:8000,http://127.0.0.1:8000,http://localhost:5173,http://127.0.0.1:5173"
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("RODEX_ALLOWED_ORIGINS", _default_origins).split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(SessionAuthMiddleware)

app.include_router(review_router)
app.include_router(stream_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


# Serve IDE page
@app.get("/ide")
async def ide_page() -> FileResponse:
    return FileResponse(UI_DIR / "ide.html")


@app.get("/login")
async def login_page() -> FileResponse:
    return FileResponse(UI_DIR / "login.html")


_FIREBASE_WEB_CONFIG = json.loads(os.environ.get("FIREBASE_WEB_CONFIG", "{}"))


@app.get("/auth/config")
async def auth_config() -> dict:
    return _FIREBASE_WEB_CONFIG


@app.post("/auth/session")
async def auth_session(request: Request, response: Response) -> dict:
    body = await request.json()
    id_token = body.get("id_token", "")
    try:
        session_value = create_session_cookie(id_token)
    except Exception:
        return JSONResponse({"detail": "Invalid token"}, status_code=401)

    response = JSONResponse({"status": "ok"})
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_value,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
    )
    return response


@app.post("/auth/logout")
async def auth_logout() -> Response:
    response = JSONResponse({"status": "ok"})
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


# Serve landing page and static assets
if UI_DIR.exists():
    app.mount("/ui", StaticFiles(directory=str(UI_DIR / "src")), name="ui-src")
    app.mount("/styles", StaticFiles(directory=str(UI_DIR / "styles")), name="styles")


@app.get("/")
async def root() -> FileResponse:
    return FileResponse(UI_DIR / "index.html")
