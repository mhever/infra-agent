"""Main Textual application."""

from __future__ import annotations

import asyncio
import sys
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import Footer

from infra_agent.analyzer import Analysis, Analyzer, AnalyzerError
from infra_agent.patterns import ToolType, detect_tool_type, match_error
from infra_agent.reader import ErrorEvent, StdinReader
from infra_agent.sanitizer import sanitize
from infra_agent.ui.analysis_pane import AnalysisPane
from infra_agent.ui.log_pane import LogPane

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from infra_agent.config import Settings

__all__ = ["InfraAgentApp"]


# ---------------------------------------------------------------------------
# Custom messages
# ---------------------------------------------------------------------------


class LogLine(Message):
    """Posted when a new line is read from stdin."""

    def __init__(self, line: str) -> None:
        super().__init__()
        self.line = line


class ErrorDetected(Message):
    """Posted when the reader detects an infrastructure error."""

    def __init__(self, event: ErrorEvent) -> None:
        super().__init__()
        self.event = event


class AnalysisReady(Message):
    """Posted when LLM analysis completes."""

    def __init__(self, analysis: Analysis) -> None:
        super().__init__()
        self.analysis = analysis


class AnalysisFailed(Message):
    """Posted when LLM analysis fails."""

    def __init__(self, error_message: str) -> None:
        super().__init__()
        self.error_message = error_message


class StreamEnded(Message):
    """Posted when stdin reaches EOF."""


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


class InfraAgentApp(App[None]):
    """Main Textual application for infra-agent."""

    TITLE = "infra-agent"

    CSS = """
    #main-container {
        layout: horizontal;
    }
    #log-pane {
        width: 60%;
    }
    #analysis-pane {
        width: 40%;
    }
    """

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("q", "quit", "Quit"),
        Binding("s", "save", "Save analysis"),
    ]

    def __init__(self, settings: Settings, *, read_stdin: bool = True) -> None:
        """Initialise the application with the given settings.

        Args:
            settings: Application configuration.
            read_stdin: Whether to read from stdin on mount. Set to
                ``False`` for testing.
        """
        super().__init__()
        self._settings = settings
        self._read_stdin = read_stdin

    def compose(self) -> ComposeResult:
        """Build the widget tree."""
        with Horizontal(id="main-container"):
            yield LogPane(id="log-pane")
            yield AnalysisPane(id="analysis-pane")
        yield Footer()

    async def on_mount(self) -> None:
        """Start the stdin reader worker once the app is mounted."""
        if not self._settings.api_key:
            pane = self.query_one("#analysis-pane", AnalysisPane)
            pane.show_error("No API key configured. Set INFRA_AGENT_API_KEY.")
        if self._read_stdin:
            self.run_worker(self._stdin_pipeline(), exclusive=True)

    # ------------------------------------------------------------------
    # Background pipeline
    # ------------------------------------------------------------------

    async def _stdin_pipeline(self) -> None:
        """Read stdin, detect errors, analyse them, and post messages."""
        loop = asyncio.get_running_loop()
        stream = asyncio.StreamReader()
        transport, _ = await loop.connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(stream),
            sys.stdin.buffer,
        )

        reader = StdinReader(context_lines=self._settings.context_lines)

        try:
            async for event in self._read_and_detect(reader, stream):
                if isinstance(event, str):
                    self.post_message(LogLine(event))
                else:
                    self.post_message(ErrorDetected(event))
        finally:
            transport.close()
            self.post_message(StreamEnded())

    async def _read_and_detect(
        self,
        reader: StdinReader,
        stream: asyncio.StreamReader,
    ) -> AsyncGenerator[str | ErrorEvent, None]:
        """Yield every line as ``str`` and errors as ``ErrorEvent``.

        We need both raw lines (for the log pane) and error events (for
        analysis).  The ``StdinReader`` normally consumes lines internally, so
        we re-implement the detection loop here to emit both.
        """
        context_after_count = reader.context_lines // 2
        buffer: deque[str] = deque(maxlen=reader.context_lines)
        line_number = 0
        detected_tool: ToolType | None = None

        pending_event: _PendingEvent | None = None
        after_remaining = 0

        async for line in reader.read_lines(stream):
            line_number += 1

            # Yield every line for log display.
            yield line

            # Tool detection.
            if detected_tool is None:
                detected_tool = detect_tool_type(line)

            if pending_event is not None:
                pending_event.context_after.append(line)
                after_remaining -= 1
                if after_remaining <= 0:
                    yield pending_event.to_event()
                    pending_event = None
                buffer.append(line)
                continue

            result = match_error(line, detected_tool)
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

    # ------------------------------------------------------------------
    # Message handlers
    # ------------------------------------------------------------------

    def on_log_line(self, message: LogLine) -> None:
        """Handle a new log line."""
        pane = self.query_one("#log-pane", LogPane)
        pane.append_line(message.line)

    async def on_error_detected(self, message: ErrorDetected) -> None:
        """Handle a detected error and start analysis.

        Note: the error and context lines are already displayed in the log
        pane via ``on_log_line`` as they stream in, so we do not re-append
        them here to avoid duplicate output.
        """
        if not self._settings.api_key:
            return

        analysis_pane = self.query_one("#analysis-pane", AnalysisPane)
        analysis_pane.show_analyzing()

        # Run analysis in a worker so we don't block the UI.
        self.run_worker(
            self._run_analysis(message.event),
            exclusive=False,
        )

    async def _run_analysis(self, event: ErrorEvent) -> None:
        """Sanitize context and call the LLM analyzer."""
        context_text = "\n".join([*event.context_before, event.error_line, *event.context_after])
        sanitized = sanitize(context_text)

        try:
            async with Analyzer(self._settings) as analyzer:
                result = await analyzer.analyze(event, sanitized.cleaned_text)
            self.post_message(AnalysisReady(result))
        except AnalyzerError as exc:
            self.post_message(AnalysisFailed(str(exc)))
        except Exception as exc:
            self.post_message(AnalysisFailed(f"Unexpected error: {exc}"))

    def on_analysis_ready(self, message: AnalysisReady) -> None:
        """Handle completed analysis."""
        pane = self.query_one("#analysis-pane", AnalysisPane)
        pane.show_analysis(message.analysis)

    def on_analysis_failed(self, message: AnalysisFailed) -> None:
        """Handle analysis failure."""
        pane = self.query_one("#analysis-pane", AnalysisPane)
        pane.show_error(message.error_message)

    def on_stream_ended(self, _message: StreamEnded) -> None:
        """Handle stdin EOF."""
        log = self.query_one("#log-pane", LogPane)
        log.append_line("[Stream ended]")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_save(self) -> None:
        """Save the most recent analysis to a file in the current directory."""
        pane = self.query_one("#analysis-pane", AnalysisPane)
        if not pane.history:
            self.notify("No analysis to save.", severity="warning")
            return
        latest = pane.history[-1]
        out = (
            f"Diagnosis:\n{latest.diagnosis}\n\n"
            f"Root Cause:\n{latest.root_cause}\n\n"
            f"Suggested Fix:\n{latest.suggested_fix}\n\n"
            f"---\n"
            f"Model: {latest.model}\n"
            f"Tokens: {latest.tokens_used}\n"
            f"Latency: {latest.latency_ms}ms\n"
        )
        path = Path.cwd() / "infra-agent-analysis.txt"
        path.write_text(out, encoding="utf-8")
        self.notify(f"Saved to {path}")


# ---------------------------------------------------------------------------
# Internal helper (mirrors reader._PendingEvent)
# ---------------------------------------------------------------------------


@dataclass
class _PendingEvent:
    """Accumulate context_after lines before emitting an ``ErrorEvent``."""

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
