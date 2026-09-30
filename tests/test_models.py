"""Tests for LLM model configuration helpers."""

from __future__ import annotations

import litellm
import pytest
from agents.extensions.models.litellm_model import LitellmModel
from agents.model_settings import ModelSettings
from agents.models import _openai_shared
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.models.openai_responses import OpenAIResponsesModel

from strix.config.models import (
    StrixProvider,
    _NonStreamingModel,
    _TurnGuardModel,
    configure_sdk_model_defaults,
    request_timeout_extra_args,
    routes_through_litellm,
    supports_strict_tool_schemas,
    uses_chat_completions_tool_schema,
)
from strix.config.settings import Settings
from strix.llm.request_log import RequestLoggingModel


def test_request_timeout_extra_args_positive() -> None:
    assert request_timeout_extra_args(300) == {"timeout": 300}
    assert request_timeout_extra_args(10) == {"timeout": 10}


def test_request_timeout_extra_args_survives_model_settings_json_dump() -> None:
    """The Chat Completions and LiteLLM paths pydantic-serialize ModelSettings for
    their tracing span; a non-JSON-serializable timeout fails every turn there."""
    settings = ModelSettings(extra_args=request_timeout_extra_args(300))
    assert settings.to_json_dict()["extra_args"] == {"timeout": 300}


@pytest.mark.parametrize("value", [None, 0, -1])
def test_request_timeout_extra_args_disabled(value: float | None) -> None:
    assert request_timeout_extra_args(value) is None


@pytest.mark.parametrize(
    "model_name",
    [
        "anthropic/claude-sonnet-4-6",
        "bedrock/anthropic.claude-opus-4-8-v1:0",
        "vertex_ai/claude-sonnet-5",
        "Sonnet-5",
    ],
)
def test_claude_routes_reject_strict_tool_schemas(model_name: str) -> None:
    assert not supports_strict_tool_schemas(model_name)


@pytest.mark.parametrize(
    "model_name",
    ["openai/gpt-5.4", "gpt-5.4", "gemini/gemini-3.1-pro-preview", "deepseek/deepseek-v4"],
)
def test_other_routes_keep_strict_tool_schemas(model_name: str) -> None:
    assert supports_strict_tool_schemas(model_name)


@pytest.mark.parametrize(
    ("model_name", "litellm"),
    [
        ("claude-sonnet-4-5", False),
        ("openai/claude-sonnet-4-5", False),
        ("any-llm/anthropic/claude-sonnet-4-5", False),
        ("anthropic/claude-sonnet-4-5", True),
        ("litellm/anthropic/claude-sonnet-4-5", True),
        ("bedrock/anthropic.claude-sonnet-4-5-20250929-v1:0", True),
        ("ollama/llama3", True),
    ],
)
def test_routes_through_litellm_matches_the_provider(
    monkeypatch: pytest.MonkeyPatch, model_name: str, litellm: bool
) -> None:
    """The helper must agree with what StrixProvider actually builds.

    Callers use it to decide whether a LiteLLM-only request field is safe to
    attach; on the SDK's own clients such a field raises TypeError mid-turn, so
    drift here breaks every request on that route.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    assert routes_through_litellm(model_name) is litellm
    try:
        model = StrixProvider().get_model(model_name)
    except ImportError:
        # any-llm's client is an optional dependency; reaching it at all already
        # proves the route is not LiteLLM's.
        assert not litellm
        return
    while isinstance(model, _NonStreamingModel | _TurnGuardModel | RequestLoggingModel):
        model = model._inner
    assert isinstance(model, LitellmModel) is litellm


def test_api_type_override_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STRIX_LLM", "gpt-4")
    monkeypatch.setenv("STRIX_API_TYPE", "chat_completions")
    assert uses_chat_completions_tool_schema("gpt-4", Settings()) is True
    monkeypatch.setenv("STRIX_LLM", "openai/gpt-4")
    monkeypatch.setenv("STRIX_API_TYPE", "responses")
    assert uses_chat_completions_tool_schema("openai/gpt-4", Settings()) is False
    monkeypatch.setenv("STRIX_LLM", "anthropic/claude-sonnet-4-5")
    assert uses_chat_completions_tool_schema("anthropic/claude-sonnet-4-5", Settings()) is True


@pytest.mark.parametrize(
    ("api_type", "expected"),
    [
        (None, OpenAIChatCompletionsModel),
        ("chat_completions", OpenAIChatCompletionsModel),
        ("responses", OpenAIResponsesModel),
    ],
)
def test_api_type_overrides_the_api_base_route(
    monkeypatch: pytest.MonkeyPatch, api_type: str | None, expected: type
) -> None:
    """``LLM_API_BASE`` defaults to chat completions. ``STRIX_API_TYPE`` must win."""
    monkeypatch.setattr(_openai_shared, "_use_responses_by_default", True)
    monkeypatch.setattr(_openai_shared, "_default_openai_client", None)
    monkeypatch.setattr(_openai_shared, "_default_openai_key", None)
    monkeypatch.setattr(litellm, "api_key", None)
    monkeypatch.setattr(litellm, "api_base", None)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "")
    monkeypatch.setenv("STRIX_LLM", "gpt-5")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_API_BASE", "https://gateway.example/v1")
    monkeypatch.delenv("STRIX_API_TYPE", raising=False)
    if api_type is not None:
        monkeypatch.setenv("STRIX_API_TYPE", api_type)
    configure_sdk_model_defaults(Settings())
    model = StrixProvider().get_model("gpt-5")
    while isinstance(model, _NonStreamingModel | _TurnGuardModel | RequestLoggingModel):
        model = model._inner
    assert isinstance(model, expected)
