"""Tests for infra_agent.analyzer — LLM integration layer."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from typing import Any

import httpx
import pytest

from infra_agent.analyzer import (
    SYSTEM_PROMPT,
    Analysis,
    Analyzer,
    AnalyzerError,
    AnthropicProvider,
    APIResponseError,
    APITimeoutError,
    OpenRouterProvider,
    ProviderResponse,
    RateLimitError,
    _build_user_prompt,
    _parse_response,
    create_provider,
)
from infra_agent.config import Settings
from infra_agent.patterns import ToolType
from infra_agent.reader import ErrorEvent

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_error_event(
    tool_type: ToolType = ToolType.TERRAFORM,
    error_line: str = "Error: resource not found",
    line_number: int = 42,
) -> ErrorEvent:
    return ErrorEvent(
        tool_type=tool_type,
        error_line=error_line,
        line_number=line_number,
        context_before=["line before 1", "line before 2"],
        context_after=["line after 1"],
        pattern_matched="terraform_error",
    )


def _openrouter_success_json(content: str = "diagnosis text", tokens: int = 100) -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"total_tokens": tokens},
    }


def _anthropic_success_json(
    content: str = "diagnosis text",
    input_tokens: int = 50,
    output_tokens: int = 60,
) -> dict[str, Any]:
    return {
        "content": [{"text": content}],
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


class TestPromptConstruction:
    """Test system and user prompt building."""

    def test_system_prompt_contains_instructions(self) -> None:
        assert "infrastructure error diagnosis" in SYSTEM_PROMPT
        assert "diagnosis" in SYSTEM_PROMPT
        assert "root cause" in SYSTEM_PROMPT
        assert "suggested fix" in SYSTEM_PROMPT

    def test_user_prompt_includes_all_fields(self) -> None:
        event = _make_error_event()
        ctx = "sanitized context here"
        prompt = _build_user_prompt(event, ctx)
        assert "terraform" in prompt
        assert "Error: resource not found" in prompt
        assert "sanitized context here" in prompt


# ---------------------------------------------------------------------------
# OpenRouterProvider
# ---------------------------------------------------------------------------


class TestOpenRouterProvider:
    """Tests for OpenRouterProvider."""

    async def test_successful_call(self) -> None:
        resp_json = _openrouter_success_json("This is the LLM reply", 150)

        async def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert body["model"] == "test-model"
            assert len(body["messages"]) == 2
            return httpx.Response(200, json=resp_json)

        provider = OpenRouterProvider(api_key="test-key", model="test-model")
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with client:
            result = await provider.call("sys", "user", client)

        assert result.content == "This is the LLM reply"
        assert result.tokens_used == 150
        assert result.model == "test-model"

    async def test_malformed_response_raises(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"bad": "data"})

        provider = OpenRouterProvider(api_key="k", model="m")
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with client:
            with pytest.raises(APIResponseError, match="Malformed"):
                await provider.call("sys", "user", client)


# ---------------------------------------------------------------------------
# AnthropicProvider
# ---------------------------------------------------------------------------


class TestAnthropicProvider:
    """Tests for AnthropicProvider."""

    async def test_successful_call(self) -> None:
        resp_json = _anthropic_success_json("Anthropic reply", 40, 80)

        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["x-api-key"] == "akey"
            assert request.headers["anthropic-version"] == "2023-06-01"
            body = json.loads(request.content)
            assert body["system"] == "sys"
            assert body["max_tokens"] == 2048
            return httpx.Response(200, json=resp_json)

        provider = AnthropicProvider(api_key="akey", model="claude-test")
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with client:
            result = await provider.call("sys", "user", client)

        assert result.content == "Anthropic reply"
        assert result.tokens_used == 120  # 40 + 80
        assert result.model == "claude-test"

    async def test_malformed_response_raises(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"content": []})

        provider = AnthropicProvider(api_key="k", model="m")
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with client:
            with pytest.raises(APIResponseError, match="Malformed"):
                await provider.call("sys", "user", client)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Test HTTP error translation."""

    async def test_timeout_raises_api_timeout_error(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out")

        provider = OpenRouterProvider(api_key="k", model="m")
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with client:
            with pytest.raises(httpx.TimeoutException):
                await provider.call("sys", "user", client)

    async def test_429_raises_rate_limit_error(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, text="rate limited")

        provider = OpenRouterProvider(api_key="k", model="m")
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with client:
            with pytest.raises(RateLimitError, match="429"):
                await provider.call("sys", "user", client)

    async def test_500_raises_api_response_error(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="internal server error")

        provider = OpenRouterProvider(api_key="k", model="m")
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with client:
            with pytest.raises(APIResponseError, match="500"):
                await provider.call("sys", "user", client)

    async def test_malformed_json_raises_api_response_error(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": [{"no_message": True}]})

        provider = OpenRouterProvider(api_key="k", model="m")
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        async with client:
            with pytest.raises(APIResponseError, match="Malformed"):
                await provider.call("sys", "user", client)


# ---------------------------------------------------------------------------
# Retry logic
# ---------------------------------------------------------------------------


class TestRetryLogic:
    """Test Analyzer retry with exponential backoff."""

    async def test_retry_429_then_success(self) -> None:
        call_count = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(429, text="rate limited")
            return httpx.Response(200, json=_openrouter_success_json("ok", 10))

        settings = Settings(provider="openrouter", api_key="k", model="m")
        analyzer = Analyzer(settings)
        # Patch the provider to use our mock transport by overriding _call_with_retry
        # Actually, we need to test via analyze or _call_with_retry with mock transport.
        # Let's monkey-patch the _provider.call to use mock transport internally.

        original_provider = analyzer._provider
        assert isinstance(original_provider, OpenRouterProvider)

        async def patched_call(
            system_prompt: str, user_prompt: str, client: httpx.AsyncClient
        ) -> ProviderResponse:
            mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            async with mock_client:
                return await original_provider.call(system_prompt, user_prompt, mock_client)

        # Replace provider with a simple wrapper
        analyzer._provider = type(  # type: ignore[assignment]
            "MockProvider",
            (),
            {"call": staticmethod(patched_call)},
        )()

        event = _make_error_event()
        result = await analyzer.analyze(event, "context")
        assert result.diagnosis  # non-empty
        assert call_count == 2

    async def test_retry_exhausted_raises_rate_limit(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, text="rate limited")

        settings = Settings(provider="openrouter", api_key="k", model="m")
        analyzer = Analyzer(settings)
        original_provider = analyzer._provider
        assert isinstance(original_provider, OpenRouterProvider)

        async def patched_call(
            system_prompt: str, user_prompt: str, client: httpx.AsyncClient
        ) -> ProviderResponse:
            mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            async with mock_client:
                return await original_provider.call(system_prompt, user_prompt, mock_client)

        analyzer._provider = type(  # type: ignore[assignment]
            "MockProvider",
            (),
            {"call": staticmethod(patched_call)},
        )()

        event = _make_error_event()
        with pytest.raises(RateLimitError):
            await analyzer.analyze(event, "context")

    async def test_timeout_no_retry(self) -> None:
        """Timeouts should not be retried — raise immediately."""
        call_count = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            raise httpx.ReadTimeout("timed out")

        settings = Settings(provider="openrouter", api_key="k", model="m")
        analyzer = Analyzer(settings)
        original_provider = analyzer._provider
        assert isinstance(original_provider, OpenRouterProvider)

        async def patched_call(
            system_prompt: str, user_prompt: str, client: httpx.AsyncClient
        ) -> ProviderResponse:
            mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            async with mock_client:
                return await original_provider.call(system_prompt, user_prompt, mock_client)

        analyzer._provider = type(  # type: ignore[assignment]
            "MockProvider",
            (),
            {"call": staticmethod(patched_call)},
        )()

        event = _make_error_event()
        with pytest.raises(APITimeoutError):
            await analyzer.analyze(event, "context")
        assert call_count == 1


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


class TestResponseParsing:
    """Test _parse_response heuristic."""

    def test_structured_response(self) -> None:
        raw = (
            "The deployment failed due to a missing IAM role.\n\n"
            "Root cause: The IAM role was deleted.\n\n"
            "Fix: Recreate the IAM role with the correct policy."
        )
        diagnosis, root_cause, fix = _parse_response(raw)
        assert "IAM role" in diagnosis
        assert "Root cause" in root_cause
        assert "Fix" in fix

    def test_unstructured_response_paragraph_split(self) -> None:
        raw = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
        diagnosis, root_cause, fix = _parse_response(raw)
        assert diagnosis == "First paragraph."
        assert root_cause == "Second paragraph."
        assert fix == "Third paragraph."

    def test_single_paragraph_fallback(self) -> None:
        raw = "Just a single line response."
        diagnosis, _root_cause, fix = _parse_response(raw)
        assert diagnosis == raw
        assert fix == "See full response above"

    def test_empty_response(self) -> None:
        diagnosis, _root_cause, _fix = _parse_response("")
        assert "No response" in diagnosis

    def test_two_paragraphs(self) -> None:
        raw = "First.\n\nSecond."
        diagnosis, root_cause, fix = _parse_response(raw)
        assert diagnosis == "First."
        assert root_cause == "Second."
        assert fix == "See full response above"


# ---------------------------------------------------------------------------
# create_provider factory
# ---------------------------------------------------------------------------


class TestCreateProvider:
    """Test provider factory function."""

    def test_openrouter(self) -> None:
        settings = Settings(provider="openrouter", api_key="k", model="m")
        p = create_provider(settings)
        assert isinstance(p, OpenRouterProvider)
        assert p.api_key == "k"
        assert p.model == "m"

    def test_anthropic(self) -> None:
        settings = Settings(provider="anthropic", api_key="k2", model="claude-3")
        p = create_provider(settings)
        assert isinstance(p, AnthropicProvider)
        assert p.api_key == "k2"

    def test_unknown_raises(self) -> None:
        settings = Settings(provider="unknown", api_key="k", model="m")
        with pytest.raises(AnalyzerError, match="Unknown provider"):
            create_provider(settings)


# ---------------------------------------------------------------------------
# Analysis dataclass
# ---------------------------------------------------------------------------


class TestAnalysisDataclass:
    """Test Analysis frozen dataclass."""

    def test_fields_set_correctly(self) -> None:
        a = Analysis(
            diagnosis="d",
            root_cause="r",
            suggested_fix="f",
            raw_response="raw",
            tokens_used=100,
            model="m",
            latency_ms=42,
        )
        assert a.diagnosis == "d"
        assert a.root_cause == "r"
        assert a.suggested_fix == "f"
        assert a.raw_response == "raw"
        assert a.tokens_used == 100
        assert a.model == "m"
        assert a.latency_ms == 42

    def test_frozen(self) -> None:
        a = Analysis(
            diagnosis="d",
            root_cause="r",
            suggested_fix="f",
            raw_response="raw",
            tokens_used=100,
            model="m",
            latency_ms=42,
        )
        with pytest.raises(FrozenInstanceError):
            a.diagnosis = "new"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ProviderResponse dataclass
# ---------------------------------------------------------------------------


class TestProviderResponseDataclass:
    """Test ProviderResponse frozen dataclass."""

    def test_fields(self) -> None:
        pr = ProviderResponse(content="c", tokens_used=10, model="m")
        assert pr.content == "c"
        assert pr.tokens_used == 10

    def test_frozen(self) -> None:
        pr = ProviderResponse(content="c", tokens_used=10, model="m")
        with pytest.raises(FrozenInstanceError):
            pr.content = "x"  # type: ignore[misc]
