"""Shared helper for streaming an LLM response to the event bus.

Agents stream their analysis so the UI can show progress live. Two
concerns are handled here so every agent behaves the same way:

* Providers differ in chunk size — Vertex returns a few large chunks
  rather than per-token deltas — so text is re-split into small,
  sentence-like fragments before being emitted.
* The models are asked to answer with a JSON array of findings, so the
  raw stream is unreadable. Findings are narrated as their fields
  arrive, giving a live "found X in Y" feed instead of a JSON dump.
"""

from __future__ import annotations

import inspect
import re

# Emit roughly a short sentence at a time: long enough to avoid flooding
# the event bus, short enough to read as live progress.
_FRAGMENT_CHARS = 100
_SENTENCE_END = re.compile(r"(?<=[.!?;:])\s+")


def _readable_fragments(text: str) -> list[str]:
    """Split text into small fragments suitable for a progress feed."""
    fragments: list[str] = []
    for sentence in _SENTENCE_END.split(text):
        sentence = sentence.strip()
        if not sentence:
            continue
        while len(sentence) > _FRAGMENT_CHARS:
            cut = sentence.rfind(" ", 0, _FRAGMENT_CHARS)
            if cut <= 0:
                cut = _FRAGMENT_CHARS
            fragments.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if sentence:
            fragments.append(sentence)
    return fragments


class _FindingNarrator:
    """Turns a streaming JSON findings array into readable progress lines.

    The models emit one object per finding. Rather than showing raw JSON,
    each finding is announced once enough of its fields have arrived.
    """

    _SEVERITY = re.compile(r'"severity"\s*:\s*"([^"]+)"')
    _CATEGORY = re.compile(r'"category"\s*:\s*"([^"]+)"')
    _LINE = re.compile(r'"line"\s*:\s*(\d+)')
    _DESCRIPTION = re.compile(r'"description"\s*:\s*"((?:[^"\\]|\\.)*)"')

    # The JSON answer begins at the first fence or bare array. Reasoning
    # before it can quote field names ("description": ...) and would
    # otherwise be re-matched on every chunk, announcing the same finding
    # hundreds of times.
    _ANSWER_START = re.compile(r"```|\[")

    def __init__(self) -> None:
        self._announced = 0

    @classmethod
    def _answer_only(cls, text: str) -> str:
        match = cls._ANSWER_START.search(text)
        return text[match.start():] if match else ""

    def new_lines(self, text: str) -> list[str]:
        """Progress lines for findings that are complete but unannounced."""
        text = self._answer_only(text)
        descriptions = self._DESCRIPTION.findall(text)
        severities = self._SEVERITY.findall(text)
        categories = self._CATEGORY.findall(text)
        lines_no = self._LINE.findall(text)

        out: list[str] = []
        while self._announced < len(descriptions):
            i = self._announced
            severity = severities[i].upper() if i < len(severities) else "ISSUE"
            category = categories[i].replace("_", " ") if i < len(categories) else "finding"
            where = f" (line {lines_no[i]})" if i < len(lines_no) else ""
            description = descriptions[i].encode().decode("unicode_escape")
            out.append(f"[{severity}] {category}{where}: {description}")
            self._announced += 1
        return out


class _ThoughtSplitter:
    """Separates a model's streamed reasoning from its JSON answer.

    With ``include_thoughts`` the provider streams reasoning as ordinary
    content ahead of the answer, marked by ``**Bold headers**``. Reasoning
    is shown live; the JSON answer is left to the finding narrator.
    """

    _HEADER = re.compile(r"\*\*(.+?)\*\*")

    def __init__(self) -> None:
        self._buffer = ""
        self._answer_started = False

    def feed(self, delta: str) -> list[str]:
        """Readable reasoning fragments contained in this delta."""
        if self._answer_started:
            return []

        self._buffer += delta
        # The JSON answer begins at the first fence or bare array/object.
        for marker in ("```", "[", "{"):
            index = self._buffer.find(marker)
            if index != -1:
                self._answer_started = True
                self._buffer = self._buffer[:index]
                break

        fragments: list[str] = []
        # Emit only whole paragraphs so sentences are never cut mid-word.
        while "\n\n" in self._buffer:
            paragraph, self._buffer = self._buffer.split("\n\n", 1)
            fragments.extend(self._render(paragraph))
        if self._answer_started and self._buffer.strip():
            fragments.extend(self._render(self._buffer))
            self._buffer = ""
        return fragments

    def flush(self) -> list[str]:
        """Whatever reasoning is still buffered when the stream ends."""
        if self._answer_started or not self._buffer.strip():
            return []
        fragments = self._render(self._buffer)
        self._buffer = ""
        return fragments

    def _render(self, paragraph: str) -> list[str]:
        text = paragraph.strip()
        if not text:
            return []
        # A bold header is the model naming its current step.
        header = self._HEADER.match(text)
        if header:
            rest = text[header.end():].strip()
            out = [f"› {header.group(1).strip()}"]
            return out + _readable_fragments(rest) if rest else out
        return _readable_fragments(text)


# Report in-flight progress at most this often, so a fast stream does not
# flood the event bus with cost updates.
_PROGRESS_EVERY_CHARS = 400


async def stream_thinking(stream, emitter, on_usage=None, on_progress=None) -> str:
    """Consume an OpenAI-style stream, emitting readable progress.

    Reasoning is streamed as it arrives so the panel fills during the
    long pause before an answer; findings are then announced from the
    JSON payload. Returns the full raw response for the caller to parse.
    ``on_usage`` receives the provider's usage object when one is sent
    (providers report it on a final, choice-less chunk).
    """
    full_response = ""
    narrator = _FindingNarrator()
    thoughts = _ThoughtSplitter()
    next_progress = _PROGRESS_EVERY_CHARS

    async for chunk in stream:
        usage = getattr(chunk, "usage", None)
        if usage is not None and on_usage is not None:
            outcome = on_usage(usage)
            if inspect.isawaitable(outcome):
                await outcome
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta.content or ""
        if not delta:
            continue
        full_response += delta

        for fragment in thoughts.feed(delta):
            await emitter.thinking(fragment)
        for line in narrator.new_lines(full_response):
            for fragment in _readable_fragments(line):
                await emitter.thinking(fragment)

        if on_progress is not None and len(full_response) >= next_progress:
            next_progress = len(full_response) + _PROGRESS_EVERY_CHARS
            outcome = on_progress(len(full_response))
            if inspect.isawaitable(outcome):
                await outcome

    for line in narrator.new_lines(full_response):
        for fragment in _readable_fragments(line):
            await emitter.thinking(fragment)

    if not narrator._announced:
        await emitter.thinking("No issues found.")

    return full_response


def summarize_response(raw: str, usage=None) -> str:
    """One-line summary of an LLM response for the tool log.

    The log previously showed only a character count, which said nothing
    about what the call actually produced.
    """
    findings = len(_FindingNarrator._DESCRIPTION.findall(raw))
    parts = [f"{findings} finding{'' if findings == 1 else 's'}"]
    if usage is not None:
        prompt = getattr(usage, "prompt_tokens", 0) or 0
        completion = getattr(usage, "completion_tokens", 0) or 0
        parts.append(f"{prompt}+{completion} tok")
        details = getattr(usage, "completion_tokens_details", None)
        reasoning = getattr(details, "reasoning_tokens", 0) or 0 if details else 0
        if reasoning:
            parts.append(f"{reasoning} reasoning")
    parts.append(f"{len(raw)} chars")
    return " · ".join(parts)


async def stream_reasoning(stream, emitter, on_progress=None):
    """Consume a tool-calling stream, emitting the model's reasoning live.

    Unlike ``stream_thinking`` the payload here is tool calls rather than
    a JSON findings array, so the assistant text is all reasoning and is
    surfaced in full. Returns ``(text, tool_calls, usage)`` where each
    tool call is ``{"id", "name", "arguments"}``.
    """
    text = ""
    usage = None
    thoughts = _ThoughtSplitter()
    next_progress = _PROGRESS_EVERY_CHARS
    # Tool calls stream in fragments keyed by index; reassemble them.
    partial: dict[int, dict] = {}

    async for chunk in stream:
        chunk_usage = getattr(chunk, "usage", None)
        if chunk_usage is not None:
            usage = chunk_usage
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta

        content = getattr(delta, "content", None) or ""
        if content:
            text += content
            for fragment in thoughts.feed(content):
                await emitter.reasoning(fragment)
            if on_progress is not None and len(text) >= next_progress:
                next_progress = len(text) + _PROGRESS_EVERY_CHARS
                outcome = on_progress(len(text))
                if inspect.isawaitable(outcome):
                    await outcome

        for call in getattr(delta, "tool_calls", None) or []:
            slot = partial.setdefault(
                call.index, {"id": "", "name": "", "arguments": ""}
            )
            if call.id:
                slot["id"] = call.id
            function = getattr(call, "function", None)
            if function is not None:
                if function.name:
                    slot["name"] = function.name
                if function.arguments:
                    slot["arguments"] += function.arguments

    # Any reasoning still buffered when the stream ends.
    for fragment in thoughts.flush():
        await emitter.reasoning(fragment)

    tool_calls = [
        call for _, call in sorted(partial.items()) if call["name"]
    ]
    return text, tool_calls, usage
