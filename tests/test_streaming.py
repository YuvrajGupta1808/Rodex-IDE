"""Tests for the shared streaming-progress helper.

Regression guard: the UI once showed raw JSON (or almost nothing) because
progress was flushed on a fixed byte count tuned for token-sized deltas.
Providers that send few large chunks must still produce readable output.
"""

import pytest

from src.agents.streaming import stream_thinking

RESPONSE = (
    '```json\n[\n'
    '  {"file": "main.py", "line": 4, "severity": "critical",'
    ' "category": "sql_injection",'
    ' "description": "Query built from an f-string.", "code_snippet": "q"},\n'
    '  {"file": "main.py", "line": 6, "severity": "high",'
    ' "category": "hardcoded_secret",'
    ' "description": "Password hardcoded in source.", "code_snippet": "p"}\n'
    ']\n```'
)


class _Delta:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.delta = _Delta(content)


class _Chunk:
    def __init__(self, content, with_choices=True):
        self.choices = [_Choice(content)] if with_choices else []


class _Stream:
    """Mimics a provider that emits a few large chunks."""

    def __init__(self, text, chunk_size):
        self._chunks = [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]

    def __aiter__(self):
        self._index = 0
        return self

    async def __anext__(self):
        if self._index >= len(self._chunks):
            raise StopAsyncIteration
        chunk = _Chunk(self._chunks[self._index])
        self._index += 1
        return chunk


class _Emitter:
    def __init__(self):
        self.messages = []

    async def thinking(self, text):
        self.messages.append(text)


@pytest.mark.asyncio
@pytest.mark.parametrize("chunk_size", [8, 200, 10_000])
async def test_narrates_findings_regardless_of_chunk_size(chunk_size):
    emitter = _Emitter()

    raw = await stream_thinking(_Stream(RESPONSE, chunk_size), emitter)

    assert raw == RESPONSE
    joined = " ".join(emitter.messages)
    assert "[CRITICAL] sql injection (line 4)" in joined
    assert "[HIGH] hardcoded secret (line 6)" in joined
    # Each finding is announced exactly once, even across chunk boundaries.
    assert joined.count("[CRITICAL]") == 1
    assert joined.count("[HIGH]") == 1


@pytest.mark.asyncio
async def test_raw_json_is_not_shown_to_the_user():
    emitter = _Emitter()

    await stream_thinking(_Stream(RESPONSE, 40), emitter)

    joined = " ".join(emitter.messages)
    assert '"severity"' not in joined
    assert "```" not in joined


@pytest.mark.asyncio
async def test_empty_findings_still_reports_progress():
    emitter = _Emitter()

    await stream_thinking(_Stream("[]", 8), emitter)

    assert emitter.messages == ["No issues found."]


@pytest.mark.asyncio
async def test_chunks_without_choices_are_skipped():
    class _WithEmpty(_Stream):
        async def __anext__(self):
            chunk = await super().__anext__()
            return _Chunk("", with_choices=False) if chunk.choices[0].delta.content == "" else chunk

    emitter = _Emitter()
    await stream_thinking(_WithEmpty(RESPONSE, 50), emitter)

    assert any("[CRITICAL]" in m for m in emitter.messages)
