"""Provider abstraction independent of any Agent framework."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from typing import Any

from agent.state import AssistantTurn


class ModelProvider(ABC):
    """Minimal interface consumed by the future agent loop."""

    @abstractmethod
    def complete(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
    ) -> AssistantTurn:
        """Return one normalized assistant turn."""

        raise NotImplementedError

    def complete_stream(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        on_content_chunk: Callable[[str], None],
        on_reasoning_chunk: Callable[[str], None] | None = None,
    ) -> AssistantTurn:
        """Fallback to one-shot delivery when native streaming is unavailable."""

        turn = self.complete(messages, tools)
        if turn.content:
            on_content_chunk(turn.content)
        reasoning = turn.protocol_message.get("reasoning_content")
        if (
            on_reasoning_chunk is not None
            and isinstance(reasoning, str)
            and reasoning
        ):
            on_reasoning_chunk(reasoning)
        return turn
