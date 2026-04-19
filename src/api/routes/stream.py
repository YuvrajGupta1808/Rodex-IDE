from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from ..dependencies import get_event_bus
from ...events.bus import AsyncEventBus

router = APIRouter(prefix="/api", tags=["stream"])


@router.get("/stream/{session_id}")
async def stream_events(
    session_id: str,
    request: Request,
    event_bus: AsyncEventBus = Depends(get_event_bus),
    last_event_id: str | None = Query(default=None, alias="lastEventId"),
) -> StreamingResponse:
    replay_from = int(last_event_id) if last_event_id and last_event_id.isdigit() else 0

    async def event_generator():
        idx = replay_from
        async for event in event_bus.subscribe(session_id, replay_from=replay_from):
            if await request.is_disconnected():
                break
            # Include event index as SSE id for reconnect
            yield f"id: {idx}\n{event.to_sse()}"
            idx += 1

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
            "Connection": "keep-alive",
        },
    )
