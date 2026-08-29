"""Network-free tests for provider normalization and environment config."""

from __future__ import annotations

from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

import pytest

from agent.state import AssistantTurn
from providers.base import ModelProvider
from providers.openai_compatible import (
    OpenAICompatibleProvider,
    ProviderConfigurationError,
    ProviderResponseError,
    ProviderStopRequested,
    create_provider_from_env,
)
from tools.schemas import get_tool_schemas


class FakeCompletions:
    """Capture request arguments and return one predefined SDK-like response."""

    def __init__(self, response: Any) -> None:
        self.response = response
        self.last_request: dict[str, Any] | None = None

    def create(self, **kwargs: Any) -> Any:
        """Return the configured response without performing network I/O."""

        self.last_request = kwargs
        return self.response


class FakeClient:
    """Minimal shape of ``OpenAI`` used by the adapter."""

    def __init__(self, response: Any) -> None:
        self.chat = SimpleNamespace(completions=FakeCompletions(response))


class RecordingClientFactory:
    """Record environment-derived client settings without building an SDK client."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> FakeClient:
        """Record constructor arguments and return an unused fake client."""

        self.calls.append(kwargs)
        response = SimpleNamespace(choices=[], usage=None)
        return FakeClient(response)


class FakeUsage:
    """SDK-like token usage object."""

    def model_dump(self, **kwargs: Any) -> dict[str, int]:
        """Return deterministic usage counters."""

        _ = kwargs
        return {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
        }


class FakeProvider(ModelProvider):
    """Deterministic provider used to prove the interface needs no network."""

    def __init__(self, turns: Sequence[AssistantTurn]) -> None:
        self.turns = list(turns)

    def complete(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
    ) -> AssistantTurn:
        """Return the next predefined assistant turn."""

        _ = (messages, tools)
        return self.turns.pop(0)


def _tool_call_response() -> Any:
    """Build an SDK-like response containing one native tool call."""

    function = SimpleNamespace(
        name="read_file",
        arguments=(
            '{"path":"main.py","start_line":1,"max_lines":20,"max_chars":2000}'
        ),
    )
    tool_call = SimpleNamespace(id="call-123", type="function", function=function)
    message = SimpleNamespace(
        content=None,
        tool_calls=[tool_call],
        reasoning_content="I should inspect the file first.",
    )
    choice = SimpleNamespace(message=message, finish_reason="tool_calls")
    return SimpleNamespace(choices=[choice], usage=FakeUsage())


def test_provider_normalizes_native_tool_calls_and_preserves_reasoning() -> None:
    client = FakeClient(_tool_call_response())
    provider = OpenAICompatibleProvider(client, "fake-model")

    turn = provider.complete(
        [{"role": "user", "content": "inspect main.py"}],
        get_tool_schemas(),
    )

    assert turn.content is None
    assert turn.tool_calls[0].id == "call-123"
    assert turn.tool_calls[0].name == "read_file"
    assert turn.protocol_message["reasoning_content"] == "I should inspect the file first."
    assert turn.protocol_message["tool_calls"][0]["function"]["name"] == "read_file"
    assert turn.finish_reason == "tool_calls"
    assert turn.usage["total_tokens"] == 120
    request = client.chat.completions.last_request
    assert request is not None
    assert request["tool_choice"] == "auto"
    assert request["stream"] is False


def test_provider_normalizes_final_text_response() -> None:
    message = SimpleNamespace(content="Done", tool_calls=None, reasoning_content=None)
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="stop")],
        usage=None,
    )
    provider = OpenAICompatibleProvider(FakeClient(response), "fake-model")

    turn = provider.complete([], get_tool_schemas())

    assert turn.content == "Done"
    assert turn.tool_calls == []
    assert turn.protocol_message == {"role": "assistant", "content": "Done"}


def test_provider_accepts_reasoning_alias_and_stores_protocol_field() -> None:
    """Normalize gateways that expose reasoning instead of reasoning_content."""

    message = SimpleNamespace(
        content="Done",
        tool_calls=None,
        reasoning_content=None,
        reasoning="continuous reasoning",
    )
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="stop")],
        usage=None,
    )
    provider = OpenAICompatibleProvider(FakeClient(response), "fake-model")

    turn = provider.complete([], get_tool_schemas())

    assert turn.protocol_message["reasoning_content"] == "continuous reasoning"


def test_provider_does_not_send_request_after_stop() -> None:
    """Reject a cancelled request before entering the SDK transport."""

    client = FakeClient(_tool_call_response())
    provider = OpenAICompatibleProvider(
        client,
        "fake-model",
        should_stop=lambda: True,
    )

    with pytest.raises(ProviderStopRequested):
        provider.complete([], get_tool_schemas())

    assert client.chat.completions.last_request is None


def test_provider_rejects_malformed_tool_call() -> None:
    malformed_call = SimpleNamespace(
        id="call-1",
        type="function",
        function=SimpleNamespace(name="read_file", arguments=""),
    )
    message = SimpleNamespace(content=None, tool_calls=[malformed_call])
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="tool_calls")],
        usage=None,
    )
    provider = OpenAICompatibleProvider(FakeClient(response), "fake-model")

    with pytest.raises(ProviderResponseError, match="non-empty strings"):
        provider.complete([], get_tool_schemas())


def test_fake_provider_returns_predefined_turn_without_network() -> None:
    expected = AssistantTurn(
        content="offline",
        tool_calls=[],
        protocol_message={"role": "assistant", "content": "offline"},
    )
    provider = FakeProvider([expected])

    assert provider.complete([], []) is expected


def test_deepseek_factory_reads_key_and_model_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = RecordingClientFactory()
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key-from-env")
    monkeypatch.setenv("AGENT_MODEL", "test-model-from-env")
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)

    provider = create_provider_from_env("deepseek", client_factory=factory)

    assert provider.model == "test-model-from-env"
    assert provider.extra_body == {"thinking": {"type": "disabled"}}
    assert provider.reasoning_effort is None
    assert factory.calls == [
        {
            "api_key": "test-key-from-env",
            "base_url": "https://api.deepseek.com",
            "max_retries": 0,
        }
    ]


def test_deepseek_goal_mode_enables_native_reasoning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enable thinking and high effort only for the deep/goal GUI mode."""

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("AGENT_MODEL", "deepseek-reasoner")
    provider = create_provider_from_env(
        "deepseek",
        mode="goal",
        client_factory=RecordingClientFactory(),
    )

    assert provider.extra_body == {"thinking": {"type": "enabled"}}
    assert provider.reasoning_effort == "high"


def test_bailian_thinking_switch_follows_agent_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Map quick/deep to Bailian's documented enable_thinking flag."""

    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://example.invalid/compatible-mode/v1")
    monkeypatch.setenv("AGENT_MODEL", "qwen-model")
    factory = RecordingClientFactory()

    quick = create_provider_from_env("bailian", mode="auto", client_factory=factory)
    deep = create_provider_from_env("bailian", mode="goal", client_factory=factory)

    assert quick.extra_body == {"enable_thinking": False}
    assert deep.extra_body == {"enable_thinking": True}


def test_factory_rejects_missing_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_MODEL", "test-model")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(ProviderConfigurationError, match="DEEPSEEK_API_KEY"):
        create_provider_from_env("deepseek", client_factory=RecordingClientFactory())


def test_bailian_factory_requires_region_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_MODEL", "test-model")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.delenv("DASHSCOPE_BASE_URL", raising=False)

    with pytest.raises(ProviderConfigurationError, match="DASHSCOPE_BASE_URL"):
        create_provider_from_env("bailian", client_factory=RecordingClientFactory())
