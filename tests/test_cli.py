"""Tests for the CLI entry point."""

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
