"""CLI entry point using click."""

import asyncio
import sys

import click

from infra_agent import __version__
from infra_agent.config import Settings
from infra_agent.reader import StdinReader


async def _run_reader(context_lines: int) -> None:
    """Read stdin asynchronously and print detected error events."""
    loop = asyncio.get_running_loop()
    stream = asyncio.StreamReader()
    transport, _ = await loop.connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(stream),
        sys.stdin.buffer,
    )
    try:
        reader = StdinReader(context_lines=context_lines)
        async for event in reader.detect_errors(stream):
            click.echo(str(event))
            click.echo()
    finally:
        transport.close()


@click.command()
@click.version_option(version=__version__, prog_name="infra-agent")
def main() -> None:
    """AI-powered infrastructure error diagnosis."""
    if sys.stdin.isatty():
        click.echo("Usage: <command> | infra-agent")
        click.echo("Pipe infrastructure command output to infra-agent for diagnosis.")
        raise SystemExit(0)

    settings = Settings()
    asyncio.run(_run_reader(settings.context_lines))
