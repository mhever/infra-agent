"""CLI entry point using click."""

import sys

import click

from infra_agent import __version__


@click.command()
@click.version_option(version=__version__, prog_name="infra-agent")
def main() -> None:
    """AI-powered infrastructure error diagnosis."""
    if sys.stdin.isatty():
        click.echo("Usage: <command> | infra-agent")
        click.echo("Pipe infrastructure command output to infra-agent for diagnosis.")
        raise SystemExit(0)

    click.echo(f"infra-agent v{__version__} — ready (reader not yet implemented)")
