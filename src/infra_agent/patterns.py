"""Error detection regex patterns per infrastructure tool."""

import re
from dataclasses import dataclass
from enum import Enum

__all__ = ["MatchResult", "ToolType", "detect_tool_type", "match_error"]


class ToolType(Enum):
    """Supported infrastructure tool types."""

    TERRAFORM = "terraform"
    PACKER = "packer"
    ANSIBLE = "ansible"
    GENERIC = "generic"


@dataclass(frozen=True, slots=True)
class MatchResult:
    """Result of matching a line against error patterns."""

    tool_type: ToolType
    pattern_name: str
    matched_text: str


# Named regex patterns per tool type.
# Each entry is (pattern_name, compiled_regex).
type PatternList = list[tuple[str, re.Pattern[str]]]

_TERRAFORM_PATTERNS: PatternList = [
    ("terraform_pipe_error", re.compile(r"\|\s*Error:")),
    ("terraform_error", re.compile(r"Error:")),
    ("terraform_on_tf_line", re.compile(r"on .*\.tf line")),
    ("terraform_failed_to", re.compile(r"Failed to")),
    (
        "terraform_timeout",
        re.compile(r"(?i)(?:timed?\s*out|timeout\s+(?:exceeded|waiting|error))"),
    ),
    ("terraform_403", re.compile(r"(?:status|HTTP|response).*\b403\b|\b403\s*Forbidden")),
    ("terraform_404", re.compile(r"(?:status|HTTP|response).*\b404\b|\b404\s*Not\s*Found")),
    ("terraform_quota_exceeded", re.compile(r"(?i)quota exceeded")),
]

_PACKER_PATTERNS: PatternList = [
    ("packer_errored", re.compile(r"==>.*errored")),
    ("packer_build_errored", re.compile(r"Build.*errored")),
    ("packer_error_launching", re.compile(r"Error launching source instance")),
    ("packer_timeout_waiting", re.compile(r"Timeout waiting for")),
]

_ANSIBLE_PATTERNS: PatternList = [
    ("ansible_fatal", re.compile(r"fatal:")),
    ("ansible_failed", re.compile(r"FAILED!")),
    ("ansible_error", re.compile(r"ERROR!")),
    ("ansible_unreachable", re.compile(r"UNREACHABLE!")),
]

_GENERIC_PATTERNS: PatternList = [
    ("generic_fatal", re.compile(r"\bFATAL\b")),
    ("generic_error", re.compile(r"\bERROR\b")),
    ("generic_panic", re.compile(r"panic:")),
    ("generic_traceback", re.compile(r"Traceback \(most recent call last\)")),
    ("generic_exception", re.compile(r"^\w+(?:\.\w+)*(?:Error|Exception):")),
    ("generic_stack_trace", re.compile(r"^\s+at\s+\S+.*\(.*:\d+\)")),
]

TOOL_PATTERNS: dict[ToolType, PatternList] = {
    ToolType.TERRAFORM: _TERRAFORM_PATTERNS,
    ToolType.PACKER: _PACKER_PATTERNS,
    ToolType.ANSIBLE: _ANSIBLE_PATTERNS,
    ToolType.GENERIC: _GENERIC_PATTERNS,
}

# Detection patterns: compiled regex -> ToolType
_DETECT_PATTERNS: list[tuple[re.Pattern[str], ToolType]] = [
    (re.compile(r"Terraform v\d+"), ToolType.TERRAFORM),
    (re.compile(r"OpenTofu v\d+"), ToolType.TERRAFORM),
    (re.compile(r"Initializing the backend"), ToolType.TERRAFORM),
    (re.compile(r"==> [\w-]+:"), ToolType.PACKER),
    (re.compile(r"==> packer|\bpacker\s+v\d+", re.IGNORECASE), ToolType.PACKER),
    (re.compile(r"PLAY \["), ToolType.ANSIBLE),
    (re.compile(r"TASK \["), ToolType.ANSIBLE),
    (re.compile(r"\bansible\b(?:-playbook|\s+v\d+|\s*\[)", re.IGNORECASE), ToolType.ANSIBLE),
]


def detect_tool_type(line: str) -> ToolType | None:
    """Auto-detect infrastructure tool type from a log line.

    Checks the line against known tool signatures such as
    ``Terraform v1.x.x``, ``==> packer``, ``PLAY [``, etc.

    Returns the detected ``ToolType`` or ``None`` if the line does not
    match any known tool signature.
    """
    for pattern, tool_type in _DETECT_PATTERNS:
        if pattern.search(line):
            return tool_type
    return None


def _match_against(line: str, tool_type: ToolType) -> MatchResult | None:
    """Check a line against all patterns for a given tool type."""
    for pattern_name, regex in TOOL_PATTERNS[tool_type]:
        m = regex.search(line)
        if m is not None:
            return MatchResult(
                tool_type=tool_type,
                pattern_name=pattern_name,
                matched_text=m.group(0),
            )
    return None


def match_error(line: str, tool_type: ToolType | None = None) -> MatchResult | None:
    """Test a line against error patterns and return the first match.

    If *tool_type* is provided, that tool's patterns are checked first,
    followed by generic patterns.  If *tool_type* is ``None``, all tool
    pattern sets are checked in order, with generic patterns last.

    Returns a ``MatchResult`` on the first match, or ``None`` if no
    pattern matched.
    """
    if tool_type is not None:
        # Check the specific tool first, then generic fallback.
        result = _match_against(line, tool_type)
        if result is not None:
            return result
        if tool_type is not ToolType.GENERIC:
            return _match_against(line, ToolType.GENERIC)
        return None

    # No tool_type hint — check all non-generic tools, then generic.
    for tt in ToolType:
        if tt is ToolType.GENERIC:
            continue
        result = _match_against(line, tt)
        if result is not None:
            return result
    return _match_against(line, ToolType.GENERIC)
