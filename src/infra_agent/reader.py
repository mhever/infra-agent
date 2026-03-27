"""Async stdin reader and error detection."""

import asyncio
from collections import deque
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field

from infra_agent.patterns import ToolType, detect_tool_type, match_error

__all__ = ["ErrorEvent", "StdinReader"]


@dataclass(frozen=True, slots=True)
class ErrorEvent:
    """An error detected in the input stream with surrounding context."""

    tool_type: ToolType
    error_line: str
    line_number: int
    context_before: list[str]
    context_after: list[str]
    pattern_matched: str

    def __str__(self) -> str:
        """Return a human-readable representation of the error event."""
        parts = [
            f"[{self.tool_type.value}] Error at line {self.line_number} "
            f"(pattern: {self.pattern_matched})"
        ]
        if self.context_before:
            parts.extend(f"  {line}" for line in self.context_before)
        parts.append(f">>> {self.error_line}")
        if self.context_after:
            parts.extend(f"  {line}" for line in self.context_after)
        return "\n".join(parts)


@dataclass
class StdinReader:
    """Reads lines from an async stream and detects infrastructure errors.

    Maintains a rolling buffer of recent lines to provide context around
    detected errors.
    """

    context_lines: int = 20
    _detected_tool: ToolType | None = field(default=None, init=False, repr=False)

    async def read_lines(self, stream: asyncio.StreamReader) -> AsyncGenerator[str, None]:
        """Yield lines as they arrive from *stream*.

        Each yielded string has the trailing newline stripped.
        """
        while True:
            raw = await stream.readline()
            if not raw:
                break
            yield raw.decode("utf-8", errors="replace").rstrip("\r\n")

    async def detect_errors(
        self, stream: asyncio.StreamReader
    ) -> AsyncGenerator[ErrorEvent, None]:
        """Read lines from *stream*, detect errors, and yield ``ErrorEvent`` objects.

        Uses a rolling buffer of the most recent lines (sized by
        ``context_lines``) to provide context before each error.  After an
        error is detected, up to ``context_lines // 2`` subsequent lines
        are collected as context after.

        Note: errors that occur within the context-after window of a
        previous error are captured as context lines, not as separate
        ``ErrorEvent`` instances.  This is by design to keep events
        non-overlapping.
        """
        # Reset detected tool so the reader is reusable across multiple streams.
        self._detected_tool = None

        context_after_count = self.context_lines // 2
        buffer: deque[str] = deque(maxlen=self.context_lines)
        line_number = 0

        # We accumulate lines via read_lines, but we need to be able to
        # "peek ahead" after finding an error.  We therefore collect into
        # a simple state machine.
        pending_event: _PendingEvent | None = None
        after_remaining = 0

        async for line in self.read_lines(stream):
            line_number += 1

            # Try to detect the tool type from early lines.
            if self._detected_tool is None:
                detected = detect_tool_type(line)
                if detected is not None:
                    self._detected_tool = detected

            if pending_event is not None:
                # We are collecting context_after lines.
                pending_event.context_after.append(line)
                after_remaining -= 1
                if after_remaining <= 0:
                    yield pending_event.to_event()
                    pending_event = None
                buffer.append(line)
                continue

            # Check for error.
            result = match_error(line, self._detected_tool)
            if result is not None:
                before = list(buffer)
                pending_event = _PendingEvent(
                    tool_type=result.tool_type,
                    error_line=line,
                    line_number=line_number,
                    context_before=before,
                    pattern_matched=result.pattern_name,
                )
                after_remaining = context_after_count
                if after_remaining == 0:
                    yield pending_event.to_event()
                    pending_event = None
            buffer.append(line)

        # Flush any pending event at end of stream.
        if pending_event is not None:
            yield pending_event.to_event()


@dataclass
class _PendingEvent:
    """Internal helper to accumulate context_after lines."""

    tool_type: ToolType
    error_line: str
    line_number: int
    context_before: list[str]
    pattern_matched: str
    context_after: list[str] = field(default_factory=list)

    def to_event(self) -> ErrorEvent:
        """Convert to a frozen ``ErrorEvent``."""
        return ErrorEvent(
            tool_type=self.tool_type,
            error_line=self.error_line,
            line_number=self.line_number,
            context_before=self.context_before,
            context_after=list(self.context_after),
            pattern_matched=self.pattern_matched,
        )
