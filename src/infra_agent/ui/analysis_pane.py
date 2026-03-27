"""Analysis pane widget for LLM output."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.containers import VerticalScroll
from textual.widgets import Markdown, Static

if TYPE_CHECKING:
    from textual.app import ComposeResult

    from infra_agent.analyzer import Analysis

__all__ = ["AnalysisPane"]


class AnalysisPane(VerticalScroll):
    """Right pane: LLM analysis with markdown rendering.

    Displays the most recent ``Analysis`` as formatted markdown, with
    metadata about the model and latency in a footer line. Previous
    analyses are stored in an internal history list.
    """

    DEFAULT_CSS = """
    AnalysisPane {
        border: solid $accent;
        border-title-color: $text;
    }
    AnalysisPane > #analysis-status {
        text-align: center;
        color: $text-muted;
        margin: 1 0;
    }
    AnalysisPane > #analysis-md {
        margin: 0 1;
    }
    """

    def __init__(self, *, id: str | None = None) -> None:
        """Initialise the analysis pane."""
        super().__init__(id=id)
        self.border_title = "Analysis"
        self._history: list[Analysis] = []

    def compose(self) -> ComposeResult:
        """Build the initial child widgets."""
        yield Static("Waiting for errors...", id="analysis-status")
        yield Markdown("", id="analysis-md")

    def show_waiting(self) -> None:
        """Reset the pane to the initial waiting state."""
        status = self.query_one("#analysis-status", Static)
        status.update("Waiting for errors...")
        md = self.query_one("#analysis-md", Markdown)
        md.update("")

    def show_analyzing(self) -> None:
        """Show a loading indicator while analysis is in progress."""
        status = self.query_one("#analysis-status", Static)
        status.update("Analyzing...")
        md = self.query_one("#analysis-md", Markdown)
        md.update("")

    def show_analysis(self, analysis: Analysis) -> None:
        """Render an ``Analysis`` result as markdown.

        Args:
            analysis: The analysis to display.
        """
        self._history.append(analysis)
        status = self.query_one("#analysis-status", Static)
        status.update("")
        md_text = (
            f"## Diagnosis\n\n{analysis.diagnosis}\n\n"
            f"## Root Cause\n\n{analysis.root_cause}\n\n"
            f"## Suggested Fix\n\n{analysis.suggested_fix}\n\n"
            f"---\n\n"
            f"*Model:* `{analysis.model}` | "
            f"*Tokens:* {analysis.tokens_used} | "
            f"*Latency:* {analysis.latency_ms}ms"
        )
        md_widget = self.query_one("#analysis-md", Markdown)
        md_widget.update(md_text)

    def show_error(self, message: str) -> None:
        """Display an error message instead of analysis.

        Args:
            message: The error text to show.
        """
        status = self.query_one("#analysis-status", Static)
        status.update("")
        md_widget = self.query_one("#analysis-md", Markdown)
        md_widget.update(f"**Error:** {message}")

    @property
    def history(self) -> list[Analysis]:
        """Return the list of past analyses (most recent last)."""
        return list(self._history)
