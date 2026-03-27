"""PII and secret sanitization before LLM transmission.

Uses detect-secrets (Yelp) as the detection engine. This module is a thin wrapper
that scans text, replaces detected secrets with [REDACTED:type] tags, and returns
structured results.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from detect_secrets import SecretsCollection  # type: ignore[attr-defined]
from detect_secrets.settings import transient_settings

__all__ = ["Redaction", "SanitizedResult", "SanitizerConfig", "sanitize"]

# Regex for URL credentials: ://user:password@host
_URL_PASSWORD_RE = re.compile(r"(://[^:/@\s]+:)([^@\s]+)(@)")

# Regex for Azure SAS tokens: ?sig=... or &sig=...
_AZURE_SAS_RE = re.compile(r"([\?&]sig=)([^&\s]+)")

# Regex for private key blocks (BEGIN through END, inclusive)
_PRIVATE_KEY_BLOCK_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)

# Regex for key=value pairs where the key suggests a secret.
# Matches: secret_key = VALUE, api_key=VALUE, PASSWORD: VALUE, etc.
# The value is captured as group 2 (non-whitespace, non-quote chars or quoted string).
_SECRET_KEYWORD_VALUE_RE = re.compile(
    r"(?i)"
    r"((?:secret|password|passwd|token|api[_-]?key|access[_-]?key|private[_-]?key"
    r"|account[_-]?key|auth|credential|secret[_-]?access[_-]?key)"
    r"""\s*[:=]\s*["']?)"""
    r"""([^\s"';,]+)""",
)

# Regex for Azure connection string AccountKey values
_AZURE_ACCOUNT_KEY_RE = re.compile(r"(AccountKey=)([^;\"'\s]+)")


@dataclass(frozen=True, slots=True)
class Redaction:
    """A single redaction performed on the input text."""

    redaction_type: str
    line_number: int
    original_length: int


@dataclass(frozen=True, slots=True)
class SanitizedResult:
    """Result of sanitizing text: cleaned text plus metadata about redactions."""

    cleaned_text: str
    redactions: tuple[Redaction, ...] = field(default_factory=tuple)

    @property
    def redaction_count(self) -> int:
        """Return the total number of redactions applied."""
        return len(self.redactions)


@dataclass(slots=True)
class SanitizerConfig:
    """Configuration for which detect-secrets plugins to enable.

    Each boolean flag controls a category of detectors. The ``entropy_limit``
    controls the Shannon-entropy threshold for high-entropy string detectors.
    """

    enable_aws: bool = True
    enable_azure: bool = True
    enable_private_keys: bool = True
    enable_high_entropy: bool = True
    enable_keywords: bool = True
    enable_common_tokens: bool = True
    entropy_limit: float = 4.5

    def to_plugins_list(self) -> list[dict[str, Any]]:
        """Build the detect-secrets plugin config list based on enabled categories.

        Returns a ``list[dict[str, Any]]`` because detect-secrets expects
        arbitrary plugin configuration dicts whose value types vary by plugin.
        This is the only place ``Any`` is used, and it is justified by the
        upstream library's untyped API.
        """
        plugins: list[dict[str, Any]] = []

        if self.enable_aws:
            plugins.append({"name": "AWSKeyDetector"})

        if self.enable_azure:
            plugins.append({"name": "AzureStorageKeyDetector"})

        if self.enable_private_keys:
            plugins.append({"name": "PrivateKeyDetector"})

        if self.enable_high_entropy:
            plugins.append({"name": "HexHighEntropyString", "limit": self.entropy_limit})
            plugins.append({"name": "Base64HighEntropyString", "limit": self.entropy_limit})

        if self.enable_keywords:
            plugins.append({"name": "KeywordDetector"})
            plugins.append({"name": "BasicAuthDetector"})

        if self.enable_common_tokens:
            plugins.append({"name": "GitHubTokenDetector"})
            plugins.append({"name": "GitLabTokenDetector"})
            plugins.append({"name": "SlackDetector"})
            plugins.append({"name": "StripeDetector"})
            plugins.append({"name": "JwtTokenDetector"})
            plugins.append({"name": "DiscordBotTokenDetector"})
            plugins.append({"name": "NpmDetector"})
            plugins.append({"name": "SendGridDetector"})
            plugins.append({"name": "TwilioKeyDetector"})
            plugins.append({"name": "MailchimpDetector"})
            plugins.append({"name": "OpenAIDetector"})
            plugins.append({"name": "PypiTokenDetector"})
            plugins.append({"name": "TelegramBotTokenDetector"})
            plugins.append({"name": "SquareOAuthDetector"})

        return plugins


def _detector_name_to_tag(detector_name: str) -> str:
    """Convert a detect-secrets detector name to a snake_case redaction tag.

    Strips common suffixes like "Detector", then converts to snake_case.

    Examples:
        >>> _detector_name_to_tag("AWS Access Key")
        'aws_access_key'
        >>> _detector_name_to_tag("Base64 High Entropy String")
        'base64_high_entropy'
        >>> _detector_name_to_tag("Private Key")
        'private_key'
        >>> _detector_name_to_tag("Secret Keyword")
        'secret_keyword'
    """
    name = detector_name
    # Strip only noise-word suffixes that don't carry semantic meaning.
    # Keep meaningful suffixes like "Key", "Token", "Credentials".
    for suffix in ("Detector", "String"):
        if name.endswith(suffix) and name != suffix:
            name = name[: -len(suffix)]
    name = name.strip()
    # Convert to snake_case: insert underscores before uppercase letters
    tag = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    tag = re.sub(r"\s+", "_", tag)
    return tag.lower().strip("_")


def _redact_private_key_blocks(text: str) -> tuple[str, list[Redaction]]:
    """Replace entire private key blocks (BEGIN through END) with a single tag.

    Returns the cleaned text and any redactions produced.
    """
    redactions: list[Redaction] = []

    def _replace_block(match: re.Match[str]) -> str:
        block = match.group(0)
        # Count lines in the block for metadata
        block_lines = block.count("\n") + 1
        # Line number of the BEGIN marker in the original text
        start_pos = match.start()
        line_number = text[:start_pos].count("\n") + 1
        redactions.append(
            Redaction(
                redaction_type="private_key",
                line_number=line_number,
                original_length=len(block),
            )
        )
        _ = block_lines  # used only for counting context
        return "[REDACTED:private_key]"

    cleaned = _PRIVATE_KEY_BLOCK_RE.sub(_replace_block, text)
    return cleaned, redactions


def _redact_url_passwords(text: str) -> tuple[str, list[Redaction]]:
    """Replace passwords in connection-string URLs with redaction tags.

    Returns the cleaned text and any redactions produced.
    """
    redactions: list[Redaction] = []

    def _replace_password(match: re.Match[str]) -> str:
        password = match.group(2)
        # Determine line number
        start_pos = match.start()
        line_number = text[:start_pos].count("\n") + 1
        redactions.append(
            Redaction(
                redaction_type="url_password",
                line_number=line_number,
                original_length=len(password),
            )
        )
        return match.group(1) + "[REDACTED:url_password]" + match.group(3)

    cleaned = _URL_PASSWORD_RE.sub(_replace_password, text)
    return cleaned, redactions


def _redact_azure_sas_tokens(text: str) -> tuple[str, list[Redaction]]:
    """Replace Azure SAS token signatures with redaction tags.

    Returns the cleaned text and any redactions produced.
    """
    redactions: list[Redaction] = []

    def _replace_sas(match: re.Match[str]) -> str:
        token = match.group(2)
        start_pos = match.start()
        line_number = text[:start_pos].count("\n") + 1
        redactions.append(
            Redaction(
                redaction_type="azure_sas_token",
                line_number=line_number,
                original_length=len(token),
            )
        )
        return match.group(1) + "[REDACTED:azure_sas_token]"

    cleaned = _AZURE_SAS_RE.sub(_replace_sas, text)
    return cleaned, redactions


def _redact_secret_keyword_values(text: str) -> tuple[str, list[Redaction]]:
    """Redact values assigned to keys that look like secrets.

    Catches cases that detect-secrets misses in multi-line context, such as
    ``aws_secret_access_key = VALUE`` or ``api_key = VALUE``.

    Only redacts values that haven't already been redacted (contain no
    ``[REDACTED:`` marker).

    Returns the cleaned text and any redactions produced.
    """
    redactions: list[Redaction] = []

    def _replace_value(match: re.Match[str]) -> str:
        value = match.group(2)
        # Skip if already redacted
        if "[REDACTED:" in value:
            return match.group(0)
        start_pos = match.start()
        line_number = text[:start_pos].count("\n") + 1
        redactions.append(
            Redaction(
                redaction_type="secret_keyword",
                line_number=line_number,
                original_length=len(value),
            )
        )
        return match.group(1) + "[REDACTED:secret_keyword]"

    cleaned = _SECRET_KEYWORD_VALUE_RE.sub(_replace_value, text)
    return cleaned, redactions


def _redact_azure_account_keys(text: str) -> tuple[str, list[Redaction]]:
    """Redact Azure connection string AccountKey values.

    Returns the cleaned text and any redactions produced.
    """
    redactions: list[Redaction] = []

    def _replace_key(match: re.Match[str]) -> str:
        value = match.group(2)
        if "[REDACTED:" in value:
            return match.group(0)
        start_pos = match.start()
        line_number = text[:start_pos].count("\n") + 1
        redactions.append(
            Redaction(
                redaction_type="azure_account_key",
                line_number=line_number,
                original_length=len(value),
            )
        )
        return match.group(1) + "[REDACTED:azure_account_key]"

    cleaned = _AZURE_ACCOUNT_KEY_RE.sub(_replace_key, text)
    return cleaned, redactions


def sanitize(text: str, config: SanitizerConfig | None = None) -> SanitizedResult:
    """Scan text for secrets and replace them with ``[REDACTED:type]`` tags.

    Uses detect-secrets as the detection engine. The text is written to a
    temporary file for scanning (detect-secrets is file-oriented), then all
    detected secrets are replaced in the output.

    Args:
        text: The input text to sanitize.
        config: Optional sanitizer configuration. Uses defaults if ``None``.

    Returns:
        A ``SanitizedResult`` containing the cleaned text and redaction metadata.
    """
    if config is None:
        config = SanitizerConfig()

    plugins = config.to_plugins_list()

    # If all detectors are disabled, return text unchanged.
    if not plugins:
        return SanitizedResult(cleaned_text=text)

    tmp_dir: str | None = None
    try:
        # H3: Use a private temporary directory instead of shared /tmp.
        tmp_dir = tempfile.mkdtemp()
        tmp_path = str(Path(tmp_dir) / "scan_input.txt")
        Path(tmp_path).write_text(text, encoding="utf-8")

        # Scan with configured plugins.
        with transient_settings({"plugins_used": plugins}):
            secrets = SecretsCollection()
            secrets.scan_file(tmp_path)

        # Collect findings grouped by line number for efficient replacement.
        redactions: list[Redaction] = []
        # Track which lines have private key findings so we skip them
        # (C2: private key blocks are handled by post-processing).
        private_key_lines: set[int] = set()
        # Map: line_number -> list of (secret_value, tag, detector_type)
        findings_by_line: dict[int, list[tuple[str | None, str, str]]] = {}

        for _filename in secrets.data:
            for potential_secret in secrets.data[_filename]:
                secret_type: str = potential_secret.type
                line_number: int = potential_secret.line_number
                secret_value: str | None = potential_secret.secret_value
                tag = _detector_name_to_tag(secret_type)

                # C2: Skip private key findings — handled by block redaction.
                if "private_key" in tag.lower() or "Private Key" in secret_type:
                    private_key_lines.add(line_number)
                    continue

                if line_number not in findings_by_line:
                    findings_by_line[line_number] = []
                findings_by_line[line_number].append((secret_value, tag, secret_type))

        # Apply replacements line by line.
        if findings_by_line:
            lines = text.split("\n")
            for line_num, replacements in findings_by_line.items():
                if 1 <= line_num <= len(lines):
                    line = lines[line_num - 1]
                    for secret_val, tag, _detector_type in replacements:
                        redact_tag = f"[REDACTED:{tag}]"
                        if secret_val is None:
                            # H1: secret_value is None — we cannot determine
                            # what to redact, so skip emitting a Redaction
                            # rather than creating false metadata.
                            continue

                        # C1: Extend short prefix matches to full token boundary.
                        # Use regex to match the secret_value followed by any
                        # contiguous non-whitespace characters.
                        extended_pattern = re.compile(re.escape(secret_val) + r"\S*")
                        match = extended_pattern.search(line)
                        if match:
                            full_secret = match.group(0)
                            original_length = len(full_secret)
                            redactions.append(
                                Redaction(
                                    redaction_type=tag,
                                    line_number=line_num,
                                    original_length=original_length,
                                )
                            )
                            # H2: Use re.sub with count=1 to replace only
                            # the first occurrence.
                            line = extended_pattern.sub(redact_tag, line, count=1)
                        else:
                            # Fallback: secret_val not found on line
                            # (should not happen, but be safe).
                            original_length = len(secret_val)
                            redactions.append(
                                Redaction(
                                    redaction_type=tag,
                                    line_number=line_num,
                                    original_length=original_length,
                                )
                            )
                            line = line.replace(secret_val, redact_tag, 1)
                    lines[line_num - 1] = line
            cleaned = "\n".join(lines)
        else:
            cleaned = text

        # C2: Post-process private key blocks (BEGIN through END, inclusive).
        cleaned, pk_redactions = _redact_private_key_blocks(cleaned)
        redactions.extend(pk_redactions)

        # C3: Post-process URL passwords in connection strings.
        cleaned, url_redactions = _redact_url_passwords(cleaned)
        redactions.extend(url_redactions)

        # C4: Post-process Azure SAS tokens.
        cleaned, sas_redactions = _redact_azure_sas_tokens(cleaned)
        redactions.extend(sas_redactions)

        # Post-process Azure connection string AccountKey values.
        cleaned, azure_key_redactions = _redact_azure_account_keys(cleaned)
        redactions.extend(azure_key_redactions)

        # Post-process secret keyword values that detect-secrets missed
        # (e.g. aws_secret_access_key in multi-line context).
        cleaned, kw_redactions = _redact_secret_keyword_values(cleaned)
        redactions.extend(kw_redactions)

        return SanitizedResult(cleaned_text=cleaned, redactions=tuple(redactions))

    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)
