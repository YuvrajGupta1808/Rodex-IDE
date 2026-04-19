from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from .routes.review import router as review_router
from .routes.stream import router as stream_router
from .dependencies import get_agent_drive, get_volume_store

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(review_router)
app.include_router(stream_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


# Serve IDE page
@app.get("/ide")
async def ide_page() -> FileResponse:
    return FileResponse(UI_DIR / "ide.html")


# Serve landing page and static assets
if UI_DIR.exists():
    app.mount("/ui", StaticFiles(directory=str(UI_DIR / "src")), name="ui-src")
    app.mount("/styles", StaticFiles(directory=str(UI_DIR / "styles")), name="styles")


@app.get("/")
async def root() -> FileResponse:
    return FileResponse(UI_DIR / "index.html")
