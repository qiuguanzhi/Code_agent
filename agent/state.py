"""Core protocol and runtime state for the coding agent."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


VALID_AGENT_MODES: frozenset[str] = frozenset({"auto", "goal"})


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A normalized native tool call returned by a model provider."""

    id: str
    name: str
    arguments_json: str


@dataclass(slots=True)
class AssistantTurn:
    """A provider-independent assistant response."""

    content: str | None
    tool_calls: list[ToolCall]
    protocol_message: dict[str, Any]
    finish_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class AgentState:
    """Mutable state owned by the future Phase 3 agent loop."""

    messages: list[dict[str, Any]] = field(default_factory=list)
    step: int = 0
    started_at: float = field(default_factory=time.monotonic)
    repeated_signatures: dict[str, int] = field(default_factory=dict)
    tool_result_cache: dict[str, str] = field(default_factory=dict)
    changed_files: dict[str, str] = field(default_factory=dict)
    last_test_result: str | None = None
    initial_snapshot: dict[str, str] = field(default_factory=dict)
    mode: str = "auto"

    def __post_init__(self) -> None:
        """Reject unsupported execution modes early."""

        if self.mode not in VALID_AGENT_MODES:
            allowed = ", ".join(sorted(VALID_AGENT_MODES))
            raise ValueError(f"mode must be one of: {allowed}")

