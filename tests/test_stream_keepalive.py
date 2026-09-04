"""The event stream must survive the long gaps between agent events.

A review can go a minute between events while an agent reasons. Without a
keepalive the idle connection is dropped and the browser never receives
review_completed — so the run button stays on "Analyzing..." and fixes are
never applied to the editor, even though the backend finished the review.
"""

import asyncio

import pytest

from src.events.bus import AsyncEventBus
from src.events.schemas import AgentEvent, EventType


def _event(session_id="s1"):
    return AgentEvent.make(EventType.THINKING, "security", session_id, {"text": "hi"})


@pytest.mark.asyncio
async def test_replayed_events_are_delivered_from_the_requested_index():
    bus = AsyncEventBus()
    for _ in range(3):
        await bus.publish(_event())

    seen = []
    stream = bus.subscribe("s1", replay_from=1)
    for _ in range(2):
        seen.append(await stream.__anext__())
    await stream.aclose()

    # Resuming from index 1 yields the 2nd and 3rd events, not the 1st.
    assert len(seen) == 2


@pytest.mark.asyncio
async def test_subscriber_is_removed_when_the_stream_closes():
    bus = AsyncEventBus()
    stream = bus.subscribe("s1")
    await bus.publish(_event())
    await stream.__anext__()

    await stream.aclose()

    assert bus._subscribers["s1"] == []


@pytest.mark.asyncio
async def test_idle_subscription_times_out_without_dying():
    """Regression: waiting with a timeout must not end the subscription.

    Polling the async generator with asyncio.wait_for cancels it, so the
    next read raises StopAsyncIteration and the client silently stops
    receiving events. open_subscription must survive an idle period.
    """
    bus = AsyncEventBus()
    subscription = bus.open_subscription("s1")

    assert await subscription.next_event(timeout=0.05) is None

    await bus.publish(_event())
    event = await subscription.next_event(timeout=1)
    assert event is not None and event.event_type == EventType.THINKING
    subscription.close()


@pytest.mark.asyncio
async def test_the_async_generator_dies_when_polled_with_a_timeout():
    """Documents why open_subscription exists."""
    bus = AsyncEventBus()
    stream = bus.subscribe("s1")

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(stream.__anext__(), timeout=0.05)

    await bus.publish(_event())
    with pytest.raises(StopAsyncIteration):
        await stream.__anext__()


@pytest.mark.asyncio
async def test_subscription_replays_history_from_an_index():
    bus = AsyncEventBus()
    for _ in range(3):
        await bus.publish(_event())

    subscription = bus.open_subscription("s1")
    assert len(subscription.replay(1)) == 2
    subscription.close()


@pytest.mark.asyncio
async def test_closing_a_subscription_removes_it_from_the_bus():
    bus = AsyncEventBus()
    subscription = bus.open_subscription("s1")

    subscription.close()

    assert bus._subscribers["s1"] == []
