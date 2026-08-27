"""Provider abstraction independent of any Agent framework."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
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

