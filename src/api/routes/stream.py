from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from ...events.bus import AsyncEventBus
from ..dependencies import get_event_bus

router = APIRouter(prefix="/api", tags=["stream"])

# Long enough to stay quiet during normal streaming, short enough to keep
# idle proxies and browsers from closing the connection mid-review.
_KEEPALIVE_SECONDS = 15.0


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
        subscription = event_bus.open_subscription(session_id)
        try:
            for event in subscription.replay(replay_from):
                yield f"id: {idx}\n{event.to_sse()}"
                idx += 1

            while True:
                # A review can go a minute between events while an agent
                # reasons. Without a keepalive the idle connection is closed
                # and the client never receives review_completed, leaving the
                # UI stuck mid-run. A timeout here must not end the
                # subscription, which is why this is not a plain `async for`.
                event = await subscription.next_event(_KEEPALIVE_SECONDS)
                if await request.is_disconnected():
                    break
                if event is None:
                    yield ": keepalive\n\n"
                    continue
                yield f"id: {idx}\n{event.to_sse()}"
                idx += 1
        finally:
            subscription.close()

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
