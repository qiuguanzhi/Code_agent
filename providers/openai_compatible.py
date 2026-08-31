"""DeepSeek and Bailian adapter using OpenAI-compatible Chat Completions."""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from openai import OpenAI

from agent.state import AssistantTurn, ToolCall
from providers.base import ModelProvider


MAX_OUTPUT_TOKENS = 16_384


class ProviderConfigurationError(ValueError):
    """Raised when required provider configuration is missing or invalid."""


class ProviderResponseError(ValueError):
    """Raised when a provider response cannot be normalized safely."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


class ProviderStopRequested(RuntimeError):
    """Raised before transport when cooperative cancellation is already set."""


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


def _response_keys(response: Any) -> list[str]:
    """Return structural response keys without serializing user/model content."""

    if isinstance(response, Mapping):
        return sorted(str(key) for key in response)
    model_fields = getattr(response, "model_fields", None)
    if isinstance(model_fields, Mapping):
        return sorted(str(key) for key in model_fields)
    values = getattr(response, "__dict__", None)
    if isinstance(values, Mapping):
        return sorted(str(key) for key in values if not str(key).startswith("_"))
    return []


def _log_response_shape(response: Any) -> None:
    """Print a content-free structural summary for empty-response diagnosis."""

    choices = _field(response, "choices", [])
    choice_count = len(choices) if isinstance(choices, Sequence) else -1
    finish_reason: Any = None
    if isinstance(choices, Sequence) and choices:
        finish_reason = _field(choices[0], "finish_reason")
    print(
        "[Cerebro::Provider] response_shape "
        f"keys={_response_keys(response)} choices={choice_count} "
        f"finish_reason={finish_reason!r}"
    )


def _can_fallback_from_stream(exc: Exception) -> bool:
    """Return true only when a gateway explicitly rejects streaming syntax.

    Transport timeouts and connection failures must escape to the bounded retry
    layer. Falling back after those errors silently doubled every timeout and
    could make a 10-second network stall look like a 40-60 second GUI freeze.
    """

    if isinstance(exc, (TypeError, NotImplementedError)):
        return True
    message = str(exc).lower()
    status_code = getattr(exc, "status_code", None)
    return (
        isinstance(status_code, int)
        and status_code in {400, 404, 405, 415, 422}
        and "stream" in message
    )


class _TaggedReasoningDemultiplexer:
    """Split compatible ``<think>`` content streams across arbitrary chunks."""

    OPEN_TAG = "<think>"
    CLOSE_TAG = "</think>"

    def __init__(self) -> None:
        self._buffer = ""
        self._inside_reasoning = False

    def feed(self, text: str) -> tuple[str, str]:
        """Return newly complete ``(content, reasoning)`` text."""

        self._buffer += text
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        while self._buffer:
            marker = self.CLOSE_TAG if self._inside_reasoning else self.OPEN_TAG
            marker_index = self._buffer.find(marker)
            if marker_index >= 0:
                self._append(
                    self._buffer[:marker_index],
                    content_parts,
                    reasoning_parts,
                )
                self._buffer = self._buffer[marker_index + len(marker) :]
                self._inside_reasoning = not self._inside_reasoning
                continue

            retained = self._possible_marker_suffix(self._buffer, marker)
            safe_length = len(self._buffer) - retained
            if safe_length:
                self._append(
                    self._buffer[:safe_length],
                    content_parts,
                    reasoning_parts,
                )
                self._buffer = self._buffer[safe_length:]
            break
        return "".join(content_parts), "".join(reasoning_parts)

    def finish(self) -> tuple[str, str]:
        """Flush text retained only because it resembled a split tag."""

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        self._append(self._buffer, content_parts, reasoning_parts)
        self._buffer = ""
        return "".join(content_parts), "".join(reasoning_parts)

    def _append(
        self,
        text: str,
        content_parts: list[str],
        reasoning_parts: list[str],
    ) -> None:
        if not text:
            return
        target = reasoning_parts if self._inside_reasoning else content_parts
        target.append(text)

    @staticmethod
    def _possible_marker_suffix(text: str, marker: str) -> int:
        """Return the longest suffix that may be the start of ``marker``."""

        maximum = min(len(text), len(marker) - 1)
        for length in range(maximum, 0, -1):
            if marker.startswith(text[-length:]):
                return length
        return 0


class OpenAICompatibleProvider(ModelProvider):
    """Normalize OpenAI-compatible native tool calls into ``AssistantTurn``."""

    def __init__(
        self,
        client: Any,
        model: str,
        *,
        extra_body: Mapping[str, Any] | None = None,
        reasoning_effort: str | None = None,
        should_stop: Callable[[], bool] | None = None,
        temperature: float = 0.0,
        max_tokens: int = MAX_OUTPUT_TOKENS,
        request_timeout_seconds: float = 120.0,
        max_completion_tokens: int = 8_192,
        supports_max_completion_tokens: bool = False,
        max_input_tokens: int = 320_000,
    ) -> None:
        if not model.strip():
            raise ProviderConfigurationError("model must be a non-empty string")
        self.client = client
        self.model = model
        self.extra_body = dict(extra_body or {})
        self.reasoning_effort = reasoning_effort
        self.should_stop = should_stop
        self.temperature = temperature
        if max_tokens < 1:
            raise ProviderConfigurationError("max_tokens must be positive")
        if request_timeout_seconds <= 0:
            raise ProviderConfigurationError("request_timeout_seconds must be positive")
        if max_completion_tokens < 1:
            raise ProviderConfigurationError("max_completion_tokens must be positive")
        if max_input_tokens < 1:
            raise ProviderConfigurationError("max_input_tokens must be positive")
        self.max_tokens = max_tokens
        self.request_timeout_seconds = request_timeout_seconds
        self.max_completion_tokens = max_completion_tokens
        self.supports_max_completion_tokens = supports_max_completion_tokens
        self.max_input_tokens = max_input_tokens

    def complete(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
    ) -> AssistantTurn:
        """Make one non-streaming request and preserve native protocol fields."""

        if self.should_stop is not None and self.should_stop():
            raise ProviderStopRequested("user requested stop before model request")
        started_at = time.perf_counter()
        print(f"[Cerebro::Provider] request model={self.model} stream=false")
        request = self._request_payload(messages, tools, stream=False)
        response = self.client.chat.completions.create(**request)
        _log_response_shape(response)
        turn = self._normalize_response(response)
        duration_ms = (time.perf_counter() - started_at) * 1_000
        print(
            "[Cerebro::Provider] "
            f"response model={self.model} stream=false duration_ms={duration_ms:.1f}"
        )
        return turn

    def complete_stream(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        on_content_chunk: Callable[[str], None],
        on_reasoning_chunk: Callable[[str], None] | None = None,
    ) -> AssistantTurn:
        """Stream text deltas and safely accumulate native tool-call fragments."""

        if self.should_stop is not None and self.should_stop():
            raise ProviderStopRequested("user requested stop before model request")
        emitted_content = ""
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_parts: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
        usage: dict[str, int] = {}
        chunk_count = 0
        choices_seen = 0
        tagged_reasoning = _TaggedReasoningDemultiplexer()
        started_at = time.perf_counter()
        print(f"[Cerebro::Provider] request model={self.model} stream=true")
        try:
            stream = self.client.chat.completions.create(
                **self._request_payload(messages, tools, stream=True)
            )
            for chunk in stream:
                chunk_count += 1
                if self.should_stop is not None and self.should_stop():
                    raise ProviderStopRequested("user requested stop during model request")
                choices = _field(chunk, "choices", []) or []
                if choices:
                    choices_seen += len(choices)
                    choice = choices[0]
                    delta = _field(choice, "delta")
                    if delta is not None:
                        reasoning_delta = _field(delta, "reasoning_content")
                        if reasoning_delta is None:
                            reasoning_delta = _field(delta, "reasoning")
                        if isinstance(reasoning_delta, str) and reasoning_delta:
                            reasoning_parts.append(reasoning_delta)
                            if on_reasoning_chunk is not None:
                                on_reasoning_chunk(reasoning_delta)
                        text_delta = _field(delta, "content")
                        if isinstance(text_delta, str) and text_delta:
                            content_delta, tagged_delta = tagged_reasoning.feed(text_delta)
                            if content_delta:
                                content_parts.append(content_delta)
                                emitted_content += content_delta
                                on_content_chunk(content_delta)
                            if tagged_delta:
                                reasoning_parts.append(tagged_delta)
                                if on_reasoning_chunk is not None:
                                    on_reasoning_chunk(tagged_delta)
                        self._accumulate_tool_deltas(
                            tool_parts,
                            _field(delta, "tool_calls", []) or [],
                        )
                    raw_finish = _field(choice, "finish_reason")
                    if raw_finish is not None:
                        finish_reason = str(raw_finish)
                chunk_usage = _usage_dict(_field(chunk, "usage"))
                if chunk_usage:
                    usage = chunk_usage
        except ProviderStopRequested:
            raise
        except Exception as exc:
            # Some OpenAI-compatible gateways reject stream=True or stream
            # tool calls. Only explicit protocol incompatibility falls back;
            # network/timeout failures are handled once by the retry layer.
            duration_ms = (time.perf_counter() - started_at) * 1_000
            print(
                "[Cerebro::Provider] "
                f"stream_error type={type(exc).__name__} duration_ms={duration_ms:.1f} "
                f"fallback={_can_fallback_from_stream(exc)}"
            )
            if not _can_fallback_from_stream(exc):
                raise
            fallback = self.complete(messages, tools)
            if not emitted_content and fallback.content:
                on_content_chunk(fallback.content)
            fallback_reasoning = fallback.protocol_message.get("reasoning_content")
            if on_reasoning_chunk is not None and isinstance(fallback_reasoning, str):
                streamed_reasoning = "".join(reasoning_parts)
                if fallback_reasoning.startswith(streamed_reasoning):
                    missing_reasoning = fallback_reasoning[len(streamed_reasoning) :]
                    if missing_reasoning:
                        on_reasoning_chunk(missing_reasoning)
            return fallback

        trailing_content, trailing_reasoning = tagged_reasoning.finish()
        if trailing_content:
            content_parts.append(trailing_content)
            emitted_content += trailing_content
            on_content_chunk(trailing_content)
        if trailing_reasoning:
            reasoning_parts.append(trailing_reasoning)
            if on_reasoning_chunk is not None:
                on_reasoning_chunk(trailing_reasoning)

        print(
            "[Cerebro::Provider] stream_shape "
            f"chunks={chunk_count} choices={choices_seen} "
            f"finish_reason={finish_reason!r}"
        )
        if choices_seen == 0:
            raise ProviderResponseError(
                "api_empty_choices",
                "streaming response contained no choices",
                details={
                    "chunks": chunk_count,
                    "choices_seen": choices_seen,
                    "finish_reason": finish_reason,
                },
            )

        normalized_calls: list[ToolCall] = []
        protocol_calls: list[dict[str, Any]] = []
        for index in sorted(tool_parts):
            item = tool_parts[index]
            call_id = item["id"] or f"call_{uuid.uuid4().hex}"
            name = item["name"]
            arguments = item["arguments"]
            if not name or not arguments:
                raise ProviderResponseError(
                    "tool_calls_parse_error",
                    "streamed tool call name and arguments must be non-empty",
                    details={"index": index, "finish_reason": finish_reason},
                )
            normalized_calls.append(ToolCall(call_id, name, arguments))
            protocol_calls.append(
                {
                    "id": call_id,
                    "type": item["type"] or "function",
                    "function": {"name": name, "arguments": arguments},
                }
            )

        content = "".join(content_parts) or None
        protocol_message: dict[str, Any] = {"role": "assistant", "content": content}
        if protocol_calls:
            protocol_message["tool_calls"] = protocol_calls
        reasoning = "".join(reasoning_parts)
        if reasoning:
            protocol_message["reasoning_content"] = reasoning
        turn = AssistantTurn(
            content=content,
            tool_calls=normalized_calls,
            protocol_message=protocol_message,
            finish_reason=finish_reason,
            usage=usage,
        )
        duration_ms = (time.perf_counter() - started_at) * 1_000
        print(
            "[Cerebro::Provider] "
            f"response model={self.model} stream=true duration_ms={duration_ms:.1f}"
        )
        return turn

    def _request_payload(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        *,
        stream: bool,
    ) -> dict[str, Any]:
        """Build one transport request without mutating caller-owned values."""

        request: dict[str, Any] = {
            "model": self.model,
            "messages": list(messages),
            "tools": list(tools),
            "tool_choice": "auto",
            "temperature": self.temperature,
            "timeout": self.request_timeout_seconds,
            "stream": stream,
            "extra_body": self.extra_body,
        }
        if self.supports_max_completion_tokens:
            request["max_completion_tokens"] = self.max_completion_tokens
        else:
            request["max_tokens"] = self.max_tokens
        if self.reasoning_effort is not None:
            request["reasoning_effort"] = self.reasoning_effort
        return request

    @staticmethod
    def _accumulate_tool_deltas(
        target: dict[int, dict[str, str]],
        raw_calls: Any,
    ) -> None:
        """Merge streamed call fragments by protocol index."""

        if not isinstance(raw_calls, Sequence):
            return
        for fallback_index, raw_call in enumerate(raw_calls):
            raw_index = _field(raw_call, "index", fallback_index)
            index = raw_index if isinstance(raw_index, int) else fallback_index
            item = target.setdefault(
                index,
                {"id": "", "type": "function", "name": "", "arguments": ""},
            )
            call_id = _field(raw_call, "id")
            call_type = _field(raw_call, "type")
            function = _field(raw_call, "function")
            name = _field(function, "name")
            arguments = _field(function, "arguments")
            if isinstance(call_id, str) and call_id:
                item["id"] = call_id
            if isinstance(call_type, str) and call_type:
                item["type"] = call_type
            if isinstance(name, str):
                item["name"] += name
            if isinstance(arguments, str):
                item["arguments"] += arguments

    def _normalize_response(self, response: Any) -> AssistantTurn:
        """Normalize one complete SDK response into the core protocol."""

        choices = _field(response, "choices", [])
        if not isinstance(choices, Sequence) or not choices:
            raise ProviderResponseError(
                "api_empty_choices",
                "provider response contains no choices",
                details={
                    "response_keys": _response_keys(response),
                    "choices_count": 0,
                },
            )

        choice = choices[0]
        message = _field(choice, "message")
        if message is None:
            raise ProviderResponseError(
                "api_missing_message",
                "provider choice contains no message",
                details={"finish_reason": _field(choice, "finish_reason")},
            )

        content = _field(message, "content")
        if content is not None and not isinstance(content, str):
            raise ProviderResponseError(
                "invalid_content_type",
                "assistant content must be text or null",
            )

        normalized_calls: list[ToolCall] = []
        protocol_calls: list[dict[str, Any]] = []
        raw_calls = _field(message, "tool_calls", []) or []
        if not isinstance(raw_calls, Sequence):
            raise ProviderResponseError(
                "tool_calls_parse_error",
                "assistant tool_calls must be a sequence",
                details={"finish_reason": _field(choice, "finish_reason")},
            )
        for raw_call in raw_calls:
            call_id = _field(raw_call, "id")
            call_type = _field(raw_call, "type", "function")
            function = _field(raw_call, "function")
            name = _field(function, "name")
            arguments = _field(function, "arguments")
            if not all(isinstance(item, str) and item for item in (call_id, name, arguments)):
                raise ProviderResponseError(
                    "tool_calls_parse_error",
                    "tool call id, name, and arguments must be non-empty strings",
                    details={"finish_reason": _field(choice, "finish_reason")},
                )
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
        if reasoning_content is None:
            reasoning_content = _field(message, "reasoning")
        if reasoning_content is not None:
            if not isinstance(reasoning_content, str):
                raise ProviderResponseError(
                    "invalid_reasoning_type",
                    "reasoning_content must be text or null",
                )
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

    def set_stop_callback(self, callback: Callable[[], bool]) -> None:
        """Attach the worker cancellation flag before the first request."""

        self.should_stop = callback

    def cancel(self) -> None:
        """Best-effort transport cancellation for an in-flight SDK request."""

        close = getattr(self.client, "close", None)
        if callable(close):
            close()


def create_provider_from_env(
    provider_name: str,
    *,
    model: str | None = None,
    mode: str = "auto",
    client_factory: Callable[..., Any] = OpenAI,
) -> OpenAICompatibleProvider:
    """Build a DeepSeek or Bailian provider using environment-only secrets."""

    normalized = provider_name.strip().lower()
    if mode not in {"auto", "goal"}:
        raise ProviderConfigurationError("mode must be 'auto' or 'goal'")
    resolved_model = model or os.getenv("AGENT_MODEL")
    if not resolved_model:
        raise ProviderConfigurationError("AGENT_MODEL is required")

    timeout_text = os.getenv("AGENT_API_TIMEOUT", "10")
    try:
        api_timeout = float(timeout_text)
    except ValueError as exc:
        raise ProviderConfigurationError("AGENT_API_TIMEOUT must be numeric") from exc
    if not 1.0 <= api_timeout <= 300.0:
        raise ProviderConfigurationError("AGENT_API_TIMEOUT must be between 1 and 300")

    request_timeout_text = os.getenv("AGENT_REQUEST_TIMEOUT", "120")
    try:
        request_timeout = float(request_timeout_text)
    except ValueError as exc:
        raise ProviderConfigurationError("AGENT_REQUEST_TIMEOUT must be numeric") from exc
    if not 1.0 <= request_timeout <= 600.0:
        raise ProviderConfigurationError(
            "AGENT_REQUEST_TIMEOUT must be between 1 and 600"
        )

    input_limit_text = os.getenv("AGENT_MODEL_INPUT_TOKENS", "320000")
    try:
        model_input_tokens = int(input_limit_text)
    except ValueError as exc:
        raise ProviderConfigurationError(
            "AGENT_MODEL_INPUT_TOKENS must be an integer"
        ) from exc
    if model_input_tokens < 1:
        raise ProviderConfigurationError("AGENT_MODEL_INPUT_TOKENS must be positive")
    supports_completion_tokens = os.getenv(
        "AGENT_USE_MAX_COMPLETION_TOKENS",
        "false",
    ).strip().lower() in {"1", "true", "yes", "on"}

    if normalized == "deepseek":
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise ProviderConfigurationError("DEEPSEEK_API_KEY is required")
        base_url = os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
        client = client_factory(
            api_key=api_key,
            base_url=base_url,
            max_retries=0,
            timeout=api_timeout,
        )
        thinking_body: dict[str, Any]
        if mode == "goal":
            thinking_body = {"thinking": {"type": "enabled"}}
        else:
            thinking_body = {"thinking": {"type": "disabled"}}
        return OpenAICompatibleProvider(
            client,
            resolved_model,
            extra_body=thinking_body,
            reasoning_effort="high" if mode == "goal" else None,
            request_timeout_seconds=request_timeout,
            supports_max_completion_tokens=supports_completion_tokens,
            max_input_tokens=model_input_tokens,
        )

    if normalized in {"bailian", "dashscope"}:
        api_key = os.getenv("DASHSCOPE_API_KEY")
        base_url = os.getenv("DASHSCOPE_BASE_URL")
        if not api_key:
            raise ProviderConfigurationError("DASHSCOPE_API_KEY is required")
        if not base_url:
            raise ProviderConfigurationError("DASHSCOPE_BASE_URL is required")
        client = client_factory(
            api_key=api_key,
            base_url=base_url,
            max_retries=0,
            timeout=api_timeout,
        )
        return OpenAICompatibleProvider(
            client,
            resolved_model,
            extra_body={"enable_thinking": mode == "goal"},
            request_timeout_seconds=request_timeout,
            supports_max_completion_tokens=supports_completion_tokens,
            max_input_tokens=model_input_tokens,
        )

    raise ProviderConfigurationError(f"unsupported provider: {provider_name}")
