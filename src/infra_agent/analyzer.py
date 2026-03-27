"""LLM API client for error analysis."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import httpx

if TYPE_CHECKING:
    from infra_agent.config import Settings
    from infra_agent.reader import ErrorEvent

__all__ = [
    "APIResponseError",
    "APITimeoutError",
    "Analysis",
    "Analyzer",
    "AnalyzerError",
    "AnthropicProvider",
    "LLMProvider",
    "OpenRouterProvider",
    "ProviderResponse",
    "RateLimitError",
    "create_provider",
]

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AnalyzerError(Exception):
    """Base exception for analyzer errors."""


class APITimeoutError(AnalyzerError):
    """Raised when the LLM API call times out."""


class APIResponseError(AnalyzerError):
    """Raised when the LLM API returns a non-2xx response or malformed data."""


class RateLimitError(AnalyzerError):
    """Raised when the LLM API returns HTTP 429 (rate limited)."""


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    """Raw response from an LLM provider."""

    content: str
    tokens_used: int
    model: str


@dataclass(frozen=True, slots=True)
class Analysis:
    """Parsed analysis of an infrastructure error."""

    diagnosis: str
    root_cause: str
    suggested_fix: str
    raw_response: str
    tokens_used: int
    model: str
    latency_ms: int


# ---------------------------------------------------------------------------
# System / user prompts
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are an infrastructure error diagnosis assistant. "
    "Analyze the following error from an infrastructure tool and provide:\n"
    "1. A concise diagnosis of what went wrong\n"
    "2. The root cause\n"
    "3. A suggested fix with code or configuration snippet if applicable\n\n"
    "Be concise and actionable. Focus on the most likely cause."
)


def _build_user_prompt(error_event: ErrorEvent, sanitized_context: str) -> str:
    """Build the user prompt from an error event and sanitized context."""
    return (
        f"Tool: {error_event.tool_type.value}\n"
        f"Error line: {error_event.error_line}\n\n"
        f"Context:\n{sanitized_context}"
    )


# ---------------------------------------------------------------------------
# Provider protocol + implementations
# ---------------------------------------------------------------------------


class LLMProvider(Protocol):
    """Protocol for LLM provider implementations."""

    async def call(
        self,
        system_prompt: str,
        user_prompt: str,
        client: httpx.AsyncClient,
    ) -> ProviderResponse:
        """Send a prompt to the LLM and return a provider response."""
        ...  # pragma: no cover


@dataclass(frozen=True, slots=True)
class OpenRouterProvider:
    """LLM provider for the OpenRouter API (OpenAI-compatible)."""

    api_key: str
    model: str

    async def call(
        self,
        system_prompt: str,
        user_prompt: str,
        client: httpx.AsyncClient,
    ) -> ProviderResponse:
        """Call the OpenRouter chat completions endpoint."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        resp = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
        )
        _check_response(resp)
        # Any is justified: httpx JSON responses have dynamic structure
        data: dict[str, object] = resp.json()
        try:
            choices = data["choices"]
            assert isinstance(choices, list)
            message = choices[0]
            assert isinstance(message, dict)
            msg_content = message["message"]
            assert isinstance(msg_content, dict)
            content = str(msg_content["content"])
            usage = data.get("usage", {})
            assert isinstance(usage, dict)
            tokens_used = int(usage.get("total_tokens", 0))
        except (KeyError, IndexError, TypeError, AssertionError) as exc:
            raise APIResponseError(f"Malformed response from OpenRouter: {exc}") from exc
        return ProviderResponse(content=content, tokens_used=tokens_used, model=self.model)


@dataclass(frozen=True, slots=True)
class AnthropicProvider:
    """LLM provider for the Anthropic Messages API."""

    api_key: str
    model: str

    async def call(
        self,
        system_prompt: str,
        user_prompt: str,
        client: httpx.AsyncClient,
    ) -> ProviderResponse:
        """Call the Anthropic messages endpoint."""
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "max_tokens": 2048,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            json=payload,
        )
        _check_response(resp)
        data: dict[str, object] = resp.json()
        try:
            content_blocks = data["content"]
            assert isinstance(content_blocks, list)
            first_block = content_blocks[0]
            assert isinstance(first_block, dict)
            content = str(first_block["text"])
            usage = data.get("usage", {})
            assert isinstance(usage, dict)
            input_tokens = int(usage.get("input_tokens", 0))
            output_tokens = int(usage.get("output_tokens", 0))
            tokens_used = input_tokens + output_tokens
        except (KeyError, IndexError, TypeError, AssertionError) as exc:
            raise APIResponseError(f"Malformed response from Anthropic: {exc}") from exc
        return ProviderResponse(content=content, tokens_used=tokens_used, model=self.model)


# ---------------------------------------------------------------------------
# Response checking helper
# ---------------------------------------------------------------------------


def _check_response(resp: httpx.Response) -> None:
    """Raise appropriate exception for non-2xx responses."""
    if resp.status_code == 429:
        raise RateLimitError(f"Rate limited (429): {resp.text}")
    if resp.status_code < 200 or resp.status_code >= 300:
        raise APIResponseError(f"HTTP {resp.status_code}: {resp.text}")


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

_SECTION_MARKERS: dict[str, list[str]] = {
    "root_cause": ["root cause", "cause"],
    "suggested_fix": ["fix", "solution", "suggestion", "remediation"],
}


def _parse_response(raw: str) -> tuple[str, str, str]:
    """Parse an LLM response into (diagnosis, root_cause, suggested_fix).

    Uses simple heuristic section detection. If structured sections are not
    found, falls back to paragraph splitting.
    """
    lines = raw.strip().splitlines()
    if not lines:
        return ("No response from LLM", "See full response above", "See full response above")

    # Try to find labelled sections (case-insensitive).
    lower_lines = [ln.lower() for ln in lines]

    def _find_section(markers: list[str]) -> int | None:
        for idx, ll in enumerate(lower_lines):
            for marker in markers:
                if marker in ll:
                    return idx
        return None

    rc_idx = _find_section(_SECTION_MARKERS["root_cause"])
    fix_idx = _find_section(_SECTION_MARKERS["suggested_fix"])

    if rc_idx is not None and fix_idx is not None and rc_idx < fix_idx:
        diagnosis = "\n".join(lines[:rc_idx]).strip()
        root_cause = "\n".join(lines[rc_idx:fix_idx]).strip()
        suggested_fix = "\n".join(lines[fix_idx:]).strip()
        if diagnosis and root_cause and suggested_fix:
            return (diagnosis, root_cause, suggested_fix)

    # Fallback: split into paragraphs.
    paragraphs: list[str] = []
    current: list[str] = []
    for line in lines:
        if line.strip() == "":
            if current:
                paragraphs.append("\n".join(current))
                current = []
        else:
            current.append(line)
    if current:
        paragraphs.append("\n".join(current))

    if len(paragraphs) >= 3:
        return (paragraphs[0], paragraphs[1], "\n".join(paragraphs[2:]))
    if len(paragraphs) == 2:
        return (paragraphs[0], paragraphs[1], "See full response above")

    # Cannot split meaningfully — everything goes into diagnosis.
    return (raw.strip(), "See full response above", "See full response above")


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------


def create_provider(config: Settings) -> OpenRouterProvider | AnthropicProvider:
    """Create an LLM provider based on configuration.

    Returns the concrete provider instance for the configured provider name.
    Raises ``AnalyzerError`` for unknown provider names.
    """
    match config.provider:
        case "openrouter":
            return OpenRouterProvider(api_key=config.api_key, model=config.model)
        case "anthropic":
            return AnthropicProvider(api_key=config.api_key, model=config.model)
        case _:
            raise AnalyzerError(f"Unknown provider: {config.provider}")


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------

_DEFAULT_TIMEOUT_S = 30.0
_MAX_RETRIES = 2
_INITIAL_BACKOFF_S = 1.0


class Analyzer:
    """Orchestrates LLM calls with retry logic and response parsing."""

    def __init__(self, config: Settings) -> None:
        self._provider: OpenRouterProvider | AnthropicProvider = create_provider(config)
        self._timeout = _DEFAULT_TIMEOUT_S

    async def analyze(
        self,
        error_event: ErrorEvent,
        sanitized_context: str,
    ) -> Analysis:
        """Analyze an error event using the configured LLM provider.

        Builds prompts, calls the provider with retry on rate-limit,
        parses the response, and tracks latency.
        """
        user_prompt = _build_user_prompt(error_event, sanitized_context)

        t0 = time.monotonic()
        provider_resp = await self._call_with_retry(SYSTEM_PROMPT, user_prompt)
        latency_ms = int((time.monotonic() - t0) * 1000)

        diagnosis, root_cause, suggested_fix = _parse_response(provider_resp.content)

        return Analysis(
            diagnosis=diagnosis,
            root_cause=root_cause,
            suggested_fix=suggested_fix,
            raw_response=provider_resp.content,
            tokens_used=provider_resp.tokens_used,
            model=provider_resp.model,
            latency_ms=latency_ms,
        )

    async def _call_with_retry(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> ProviderResponse:
        """Call the provider, retrying on rate-limit with exponential backoff."""
        import asyncio

        backoff = _INITIAL_BACKOFF_S
        last_exc: RateLimitError | None = None

        for attempt in range(_MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    return await self._provider.call(system_prompt, user_prompt, client)
            except httpx.TimeoutException as exc:
                raise APITimeoutError(f"LLM API call timed out after {self._timeout}s") from exc
            except RateLimitError as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(backoff)
                    backoff *= 2

        # Exhausted retries — re-raise the last rate-limit error.
        assert last_exc is not None
        raise last_exc
