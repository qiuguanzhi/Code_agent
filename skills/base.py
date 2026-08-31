"""Framework-free contracts for reusable Agent skills."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ToolExecutor = Callable[[str, dict[str, Any]], dict[str, Any]]
UpdateEmitter = Callable[[str, str, dict[str, Any]], None]


@dataclass(slots=True)
class AgentContext:
    """Narrow capability object supplied to a skill at execution time."""

    workspace: Path
    execute_tool: ToolExecutor
    emit_update: UpdateEmitter | None = None

    def call_tool(self, name: str, params: dict[str, Any]) -> dict[str, Any]:
        """Invoke one validated native tool through the owning registry."""

        return self.execute_tool(name, params)

    def log(self, event: str, message: str, **data: Any) -> None:
        """Publish an optional structured skill lifecycle update."""

        if self.emit_update is not None:
            self.emit_update(event, message, data)


@dataclass(slots=True)
class SkillResult:
    """Provider-neutral result returned by a skill implementation."""

    ok: bool
    data: Any = None
    error: dict[str, Any] | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def as_tool_result(self) -> dict[str, Any]:
        """Convert the result to the same JSON shape as native tools."""

        return {
            "ok": self.ok,
            "data": self.data,
            "error": self.error,
            "meta": {**self.meta, "kind": "skill"},
        }


class Skill(ABC):
    """Base class for a model-callable expert capability package."""

    name: str = ""
    description: str = ""
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    required_permissions: frozenset[str] = frozenset()
    high_risk: bool = False

    @abstractmethod
    def execute(self, params: dict[str, Any], context: AgentContext) -> SkillResult:
        """Execute the skill using only capabilities exposed by ``context``."""

