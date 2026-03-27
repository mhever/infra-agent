"""Tests for the CLI entry point."""

from unittest.mock import patch

from click.testing import CliRunner

from infra_agent import __version__
from infra_agent.cli import main


def test_version_flag() -> None:
    """--version outputs the correct version string."""
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output
    assert "infra-agent" in result.output


def test_tty_help_message() -> None:
    """When stdin is a TTY, show usage instructions and exit cleanly."""
    runner = CliRunner()
    # CliRunner doesn't provide a real TTY, so we patch isatty.
    with patch("infra_agent.cli.sys") as mock_sys:
        mock_sys.stdin.isatty.return_value = True
        result = runner.invoke(main, [])
    assert result.exit_code == 0
    assert "Usage:" in result.output
    assert "Pipe infrastructure command output" in result.output
