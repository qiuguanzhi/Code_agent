"""DeepSeek and Bailian adapter using OpenAI-compatible Chat Completions."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from openai import OpenAI

from agent.state import AssistantTurn, ToolCall
from providers.base import ModelProvider


class ProviderConfigurationError(ValueError):
    """Raised when required provider configuration is missing or invalid."""


class ProviderResponseError(ValueError):
    """Raised when a provider response cannot be normalized safely."""


def _field(value: Any, name: str, default: Any = None) -> Any:
    """Read a field from an SDK object or a plain mapping."""

    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _usage_dict(usage: Any) -> dict[str, int]:
    """Normalize SDK usage metadata to integer counters."""

    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):
        raw = usage.model_dump(exclude_none=True)
    elif isinstance(usage, Mapping):
        raw = dict(usage)
    else:
        return {}
    return {str(key): value for key, value in raw.items() if isinstance(value, int)}


class OpenAICompatibleProvider(ModelProvider):
    """Normalize OpenAI-compatible native tool calls into ``AssistantTurn``."""

    def __init__(
        self,
        client: Any,
        model: str,
        *,
        extra_body: Mapping[str, Any] | None = None,
        temperature: float = 0.0,
    ) -> None:
        if not model.strip():
            raise ProviderConfigurationError("model must be a non-empty string")
        self.client = client
        self.model = model
        self.extra_body = dict(extra_body or {})
        self.temperature = temperature

    def complete(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
    ) -> AssistantTurn:
        """Make one non-streaming request and preserve native protocol fields."""

        response = self.client.chat.completions.create(
            model=self.model,
            messages=list(messages),
            tools=list(tools),
            tool_choice="auto",
            temperature=self.temperature,
            stream=False,
            extra_body=self.extra_body,
        )
        choices = _field(response, "choices", [])
        if not isinstance(choices, Sequence) or not choices:
            raise ProviderResponseError("provider response contains no choices")

        choice = choices[0]
        message = _field(choice, "message")
        if message is None:
            raise ProviderResponseError("provider choice contains no message")

        content = _field(message, "content")
        if content is not None and not isinstance(content, str):
            raise ProviderResponseError("assistant content must be text or null")

        normalized_calls: list[ToolCall] = []
        protocol_calls: list[dict[str, Any]] = []
        raw_calls = _field(message, "tool_calls", []) or []
        if not isinstance(raw_calls, Sequence):
            raise ProviderResponseError("assistant tool_calls must be a sequence")
        for raw_call in raw_calls:
            call_id = _field(raw_call, "id")
            call_type = _field(raw_call, "type", "function")
            function = _field(raw_call, "function")
            name = _field(function, "name")
            arguments = _field(function, "arguments")
            if not all(isinstance(item, str) and item for item in (call_id, name, arguments)):
                raise ProviderResponseError("tool call id, name, and arguments must be non-empty strings")
            normalized_calls.append(
                ToolCall(id=call_id, name=name, arguments_json=arguments)
            )
            protocol_calls.append(
                {
                    "id": call_id,
                    "type": call_type if isinstance(call_type, str) else "function",
                    "function": {"name": name, "arguments": arguments},
                }
            )

        protocol_message: dict[str, Any] = {
            "role": "assistant",
            "content": content,
        }
        if protocol_calls:
            protocol_message["tool_calls"] = protocol_calls

        reasoning_content = _field(message, "reasoning_content")
        if reasoning_content is not None:
            if not isinstance(reasoning_content, str):
                raise ProviderResponseError("reasoning_content must be text or null")
            protocol_message["reasoning_content"] = reasoning_content

        finish_reason = _field(choice, "finish_reason")
        if finish_reason is not None and not isinstance(finish_reason, str):
            finish_reason = str(finish_reason)

        return AssistantTurn(
            content=content,
            tool_calls=normalized_calls,
            protocol_message=protocol_message,
            finish_reason=finish_reason,
            usage=_usage_dict(_field(response, "usage")),
        )


def create_provider_from_env(
    provider_name: str,
    *,
    model: str | None = None,
    client_factory: Callable[..., Any] = OpenAI,
) -> OpenAICompatibleProvider:
    """Build a DeepSeek or Bailian provider using environment-only secrets."""

    normalized = provider_name.strip().lower()
    resolved_model = model or os.getenv("AGENT_MODEL")
    if not resolved_model:
        raise ProviderConfigurationError("AGENT_MODEL is required")

    if normalized == "deepseek":
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise ProviderConfigurationError("DEEPSEEK_API_KEY is required")
        base_url = os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
        client = client_factory(api_key=api_key, base_url=base_url, max_retries=0)
        return OpenAICompatibleProvider(client, resolved_model)

    if normalized in {"bailian", "dashscope"}:
        api_key = os.getenv("DASHSCOPE_API_KEY")
        base_url = os.getenv("DASHSCOPE_BASE_URL")
        if not api_key:
            raise ProviderConfigurationError("DASHSCOPE_API_KEY is required")
        if not base_url:
            raise ProviderConfigurationError("DASHSCOPE_BASE_URL is required")
        client = client_factory(api_key=api_key, base_url=base_url, max_retries=0)
        return OpenAICompatibleProvider(
            client,
            resolved_model,
            extra_body={"enable_thinking": False},
        )

    raise ProviderConfigurationError(f"unsupported provider: {provider_name}")

