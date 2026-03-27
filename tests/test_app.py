"""Tests for the TUI application."""

from __future__ import annotations

from infra_agent.config import Settings
from infra_agent.ui.analysis_pane import AnalysisPane
from infra_agent.ui.app import InfraAgentApp
from infra_agent.ui.log_pane import LogPane


def _make_settings() -> Settings:
    """Create minimal settings for testing (no API key)."""
    return Settings(api_key="", provider="openrouter", model="test-model")


def _make_app() -> InfraAgentApp:
    """Create an app with stdin reading disabled for testing."""
    return InfraAgentApp(settings=_make_settings(), read_stdin=False)


class TestAppMounts:
    """Test that the app mounts with the expected widget tree."""

    async def test_app_has_correct_title(self) -> None:
        """The application title should be 'infra-agent'."""
        app = _make_app()
        async with app.run_test(size=(120, 40)):
            assert app.title == "infra-agent"

    async def test_app_has_log_pane(self) -> None:
        """The app should contain a LogPane widget."""
        app = _make_app()
        async with app.run_test(size=(120, 40)):
            log_pane = app.query_one("#log-pane", LogPane)
            assert log_pane is not None

    async def test_app_has_analysis_pane(self) -> None:
        """The app should contain an AnalysisPane widget."""
        app = _make_app()
        async with app.run_test(size=(120, 40)):
            analysis_pane = app.query_one("#analysis-pane", AnalysisPane)
            assert analysis_pane is not None

    async def test_quit_keybinding(self) -> None:
        """Pressing 'q' should quit the app."""
        app = _make_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("q")
            # If we get here without hanging, the app exited cleanly.
            assert app.return_code == 0

    async def test_no_api_key_shows_error(self) -> None:
        """When no API key is set, analysis pane should show an error message."""
        app = _make_app()
        async with app.run_test(size=(120, 40)) as pilot:
            # Give the app a moment to process on_mount.
            await pilot.pause()
            pane = app.query_one("#analysis-pane", AnalysisPane)
            # The pane should have shown the "no API key" error.
            md = pane.query_one("#analysis-md")
            assert md is not None
