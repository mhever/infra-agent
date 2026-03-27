"""Tests for the async stdin reader."""

import asyncio
from pathlib import Path

from infra_agent.patterns import ToolType
from infra_agent.reader import StdinReader

FIXTURES = Path(__file__).parent / "fixtures"


def _make_stream(text: str) -> asyncio.StreamReader:
    """Create an ``asyncio.StreamReader`` pre-loaded with *text*."""
    reader = asyncio.StreamReader()
    reader.feed_data(text.encode("utf-8"))
    reader.feed_eof()
    return reader


def _make_stream_from_lines(lines: list[str]) -> asyncio.StreamReader:
    """Create an ``asyncio.StreamReader`` from a list of lines."""
    return _make_stream("\n".join(lines) + "\n")


# --- read_lines ---


class TestReadLines:
    """Tests for StdinReader.read_lines."""

    async def test_yields_all_lines(self) -> None:
        """All lines in the stream are yielded."""
        stream = _make_stream_from_lines(["line 1", "line 2", "line 3"])
        reader = StdinReader()
        lines = [line async for line in reader.read_lines(stream)]
        assert lines == ["line 1", "line 2", "line 3"]

    async def test_empty_stream(self) -> None:
        """Empty stream yields nothing."""
        stream = _make_stream("")
        reader = StdinReader()
        lines = [line async for line in reader.read_lines(stream)]
        assert lines == []

    async def test_strips_newlines(self) -> None:
        """Trailing newlines are stripped from yielded lines."""
        stream = _make_stream("hello\nworld\n")
        reader = StdinReader()
        lines = [line async for line in reader.read_lines(stream)]
        assert lines == ["hello", "world"]


# --- detect_errors with terraform fixture ---


class TestDetectErrorsTerraform:
    """Test error detection against realistic Terraform output."""

    async def test_detects_terraform_error(self) -> None:
        """Detects the error in the terraform fixture."""
        text = (FIXTURES / "terraform_error.txt").read_text()
        stream = _make_stream(text)
        reader = StdinReader(context_lines=20)
        events = [event async for event in reader.detect_errors(stream)]
        # Should detect at least the "Error:" line
        assert len(events) >= 1
        first = events[0]
        assert first.tool_type == ToolType.TERRAFORM
        assert "Error:" in first.error_line
        assert first.pattern_matched == "terraform_error"

    async def test_terraform_tool_detection(self) -> None:
        """Tool type is auto-detected as Terraform from the version line."""
        text = (FIXTURES / "terraform_error.txt").read_text()
        stream = _make_stream(text)
        reader = StdinReader(context_lines=20)
        events = [event async for event in reader.detect_errors(stream)]
        # The first error should be detected as Terraform
        assert events[0].tool_type == ToolType.TERRAFORM

    async def test_terraform_context_before(self) -> None:
        """Context before the error contains preceding lines."""
        text = (FIXTURES / "terraform_error.txt").read_text()
        stream = _make_stream(text)
        reader = StdinReader(context_lines=20)
        events = [event async for event in reader.detect_errors(stream)]
        first = events[0]
        assert len(first.context_before) > 0
        # The line before the error should be about creating a resource
        assert any("Creating" in line for line in first.context_before)

    async def test_terraform_line_number(self) -> None:
        """Error line number is correct."""
        text = (FIXTURES / "terraform_error.txt").read_text()
        stream = _make_stream(text)
        reader = StdinReader(context_lines=20)
        events = [event async for event in reader.detect_errors(stream)]
        first = events[0]
        # The "Error:" line is line 15 in the fixture (including blank line after Creating...)
        assert first.line_number == 15


# --- detect_errors with packer fixture ---


class TestDetectErrorsPacker:
    """Test error detection against realistic Packer output."""

    async def test_detects_packer_error(self) -> None:
        """Detects errors in the packer fixture."""
        text = (FIXTURES / "packer_error.txt").read_text()
        stream = _make_stream(text)
        reader = StdinReader(context_lines=10)
        events = [event async for event in reader.detect_errors(stream)]
        assert len(events) >= 1
        # Should detect the "Error launching source instance" line
        error_lines = [e.error_line for e in events]
        assert any("Error launching source instance" in line for line in error_lines)

    async def test_packer_tool_type(self) -> None:
        """Tool type is detected as Packer."""
        text = (FIXTURES / "packer_error.txt").read_text()
        stream = _make_stream(text)
        reader = StdinReader(context_lines=10)
        events = [event async for event in reader.detect_errors(stream)]
        # At least one event should be packer-typed
        assert any(e.tool_type == ToolType.PACKER for e in events)


# --- no errors ---


class TestDetectErrorsNoErrors:
    """Test that clean output produces no events."""

    async def test_no_errors(self) -> None:
        """Clean log output yields no error events."""
        lines = [
            "Terraform v1.9.5",
            "Initializing the backend...",
            "Initializing provider plugins...",
            "Terraform has been successfully initialized!",
            "Apply complete! Resources: 1 added, 0 changed, 0 destroyed.",
        ]
        stream = _make_stream_from_lines(lines)
        reader = StdinReader()
        events = [event async for event in reader.detect_errors(stream)]
        assert events == []


# --- context window ---


class TestContextWindow:
    """Test the context_lines parameter behaviour."""

    async def test_context_before_max_size(self) -> None:
        """Context before is bounded by context_lines."""
        lines = [f"line {i}" for i in range(50)]
        lines.append("FATAL: something broke")
        stream = _make_stream_from_lines(lines)
        reader = StdinReader(context_lines=10)
        events = [event async for event in reader.detect_errors(stream)]
        assert len(events) == 1
        assert len(events[0].context_before) == 10

    async def test_error_on_first_line(self) -> None:
        """Error on the very first line has empty context_before."""
        lines = ["FATAL: immediate failure", "some aftermath"]
        stream = _make_stream_from_lines(lines)
        reader = StdinReader(context_lines=4)
        events = [event async for event in reader.detect_errors(stream)]
        assert len(events) >= 1
        assert events[0].context_before == []
        assert "FATAL" in events[0].error_line

    async def test_two_consecutive_errors(self) -> None:
        """Two errors far enough apart both produce ErrorEvents."""
        lines = [
            "line 1",
            "FATAL: first error",
            "detail 1",
            "detail 2",
            "detail 3",
            "FATAL: second error",
            "after second",
            "after second 2",
            "after second 3",
        ]
        stream = _make_stream_from_lines(lines)
        # context_lines=2 -> context_after=1, so after collecting 1 line
        # the first event is emitted and the second error is not swallowed.
        reader = StdinReader(context_lines=2)
        events = [event async for event in reader.detect_errors(stream)]
        assert len(events) == 2
        assert "first error" in events[0].error_line
        assert "second error" in events[1].error_line

    async def test_error_on_last_line(self) -> None:
        """Error on the last line (EOF immediately after) still emits an event."""
        lines = ["line 1", "line 2", "FATAL: last line error"]
        stream = _make_stream_from_lines(lines)
        reader = StdinReader(context_lines=4)
        events = [event async for event in reader.detect_errors(stream)]
        assert len(events) == 1
        assert "last line error" in events[0].error_line
        # context_after should be empty since EOF follows immediately
        assert events[0].context_after == []

    async def test_context_after_collected(self) -> None:
        """Context after lines are collected correctly."""
        lines = [
            "some normal output",
            "FATAL: crash happened",
            "detail line 1",
            "detail line 2",
            "detail line 3",
        ]
        stream = _make_stream_from_lines(lines)
        reader = StdinReader(context_lines=4)  # after = 4 // 2 = 2
        events = [event async for event in reader.detect_errors(stream)]
        assert len(events) == 1
        assert events[0].context_after == ["detail line 1", "detail line 2"]
