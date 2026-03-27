"""Log pane widget for streaming output."""

from textual.widgets import RichLog

__all__ = ["LogPane"]


class LogPane(RichLog):
    """Left pane: scrolling log output with error highlighting.

    Wraps ``textual.widgets.RichLog`` to provide helper methods for
    appending lines with error/context styling.
    """

    DEFAULT_CSS = """
    LogPane {
        border: solid $accent;
        border-title-color: $text;
    }
    """

    def __init__(self, *, id: str | None = None) -> None:
        """Initialise the log pane."""
        super().__init__(
            highlight=True,
            markup=True,
            wrap=True,
            auto_scroll=True,
            max_lines=10_000,
            id=id,
        )
        self.border_title = "Log Output"

    def append_line(
        self,
        line: str,
        *,
        is_error: bool = False,
        is_context: bool = False,
    ) -> None:
        """Append a line with optional error or context styling.

        Args:
            line: The text to display.
            is_error: If ``True``, render in bold red.
            is_context: If ``True``, render in dim yellow.
        """
        # Escape Rich markup characters in the raw line to prevent
        # user-supplied text from being interpreted as markup.
        escaped = line.replace("[", "\\[")
        if is_error:
            self.write(f"[bold red]{escaped}[/bold red]")
        elif is_context:
            self.write(f"[dim yellow]{escaped}[/dim yellow]")
        else:
            self.write(escaped)
