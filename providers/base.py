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
        on_token: Callable[[str], None],
    ) -> AssistantTurn:
        """Fallback streaming API for providers without native chunk support."""

        turn = self.complete(messages, tools)
        if turn.content:
            on_token(turn.content)
        return turn
