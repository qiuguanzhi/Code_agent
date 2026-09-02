"""Framework-free contracts for reusable Agent skills."""

from __future__ import annotations

import ast
import builtins
import inspect
import json
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ToolExecutor = Callable[[str, dict[str, Any]], dict[str, Any]]
UpdateEmitter = Callable[[str, str, dict[str, Any]], None]
SKILL_METADATA_PREFIX = "# CEREBRO_SKILL_META: "


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
    built_in: bool = False
    source_path: Path | None = None

    @classmethod
    def from_code(
        cls,
        source_code: str,
        *,
        name: str,
        description: str,
        required_permissions: Iterable[str] = (),
        source_path: Path | None = None,
    ) -> "Skill":
        """Compile one concrete Skill in a deliberately restricted namespace.

        This is defense in depth rather than a process sandbox. Direct imports,
        file access, process execution, dynamic evaluation, and unrestricted
        builtins are not injected. Runtime capabilities must go through
        ``AgentContext.call_tool`` so normal permission checks still apply.
        """

        if not isinstance(source_code, str) or not source_code.strip():
            raise ValueError("Skill 执行逻辑不能为空")
        try:
            syntax_tree = ast.parse(source_code, filename="<user-skill>", mode="exec")
            compiled = compile(source_code, "<user-skill>", "exec")
        except SyntaxError as exc:
            line = exc.lineno or 0
            raise ValueError(f"Skill 代码语法错误（第 {line} 行）：{exc.msg}") from exc
        for node in ast.walk(syntax_tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                raise ValueError(
                    "动态 Skill 禁止 import；请通过 context.call_tool() 使用已授权能力"
                )
            if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
                raise ValueError("动态 Skill 禁止访问双下划线内部属性")
            if isinstance(node, ast.Name) and node.id == "__builtins__":
                raise ValueError("动态 Skill 禁止访问 __builtins__")

        safe_builtins: dict[str, Any] = {
            "__build_class__": builtins.__build_class__,
            "bool": bool,
            "classmethod": classmethod,
            "dict": dict,
            "enumerate": enumerate,
            "Exception": Exception,
            "float": float,
            "frozenset": frozenset,
            "int": int,
            "isinstance": isinstance,
            "len": len,
            "list": list,
            "max": max,
            "min": min,
            "object": object,
            "property": property,
            "range": range,
            "set": set,
            "sorted": sorted,
            "str": str,
            "staticmethod": staticmethod,
            "sum": sum,
            "tuple": tuple,
            "ValueError": ValueError,
        }
        namespace: dict[str, Any] = {
            "__builtins__": safe_builtins,
            "__name__": "cerebro_user_skill",
            "AgentContext": AgentContext,
            "Any": Any,
            "Skill": cls,
            "SkillResult": SkillResult,
        }
        try:
            exec(compiled, namespace, namespace)
        except Exception as exc:
            raise ValueError(
                f"Skill 代码加载失败：{type(exc).__name__}: {exc}"
            ) from exc

        candidates = [
            candidate
            for candidate in namespace.values()
            if inspect.isclass(candidate)
            and candidate is not cls
            and issubclass(candidate, cls)
            and candidate.__module__ == "cerebro_user_skill"
            and not inspect.isabstract(candidate)
        ]
        unique_candidates = list(dict.fromkeys(candidates))
        if not unique_candidates:
            raise ValueError("未检测到有效的 Skill 定义")
        if len(unique_candidates) > 1:
            raise ValueError("执行逻辑中只能定义一个具体 Skill 类")
        try:
            skill = unique_candidates[0]()
        except Exception as exc:
            raise ValueError(
                f"Skill 实例化失败：{type(exc).__name__}: {exc}"
            ) from exc
        skill.name = name.strip()
        skill.description = description.strip()
        skill.required_permissions = frozenset(required_permissions)
        skill.built_in = False
        skill.source_path = source_path
        return skill

    @classmethod
    def from_file(cls, file_path: Path | str) -> "Skill":
        """Reload one app-managed user Skill and its persisted form metadata."""

        try:
            resolved = Path(file_path).resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValueError("Skill 文件无法解析") from exc
        if not resolved.is_file() or resolved.suffix.casefold() != ".py":
            raise ValueError("Skill 必须是现有的 .py 文件")
        if resolved.stat().st_size > 1_000_000:
            raise ValueError("Skill 文件不能超过 1 MB")
        source_code = resolved.read_text(encoding="utf-8")
        first_line, _, _ = source_code.partition("\n")
        if not first_line.startswith(SKILL_METADATA_PREFIX):
            raise ValueError("缺少 Cerebro Skill 元数据，拒绝加载非托管文件")
        try:
            metadata = json.loads(first_line[len(SKILL_METADATA_PREFIX) :])
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError("Cerebro Skill 元数据损坏") from exc
        if not isinstance(metadata, dict):
            raise ValueError("Cerebro Skill 元数据必须是对象")
        name = metadata.get("name")
        description = metadata.get("description")
        permissions = metadata.get("permissions", [])
        if not isinstance(name, str) or not isinstance(description, str):
            raise ValueError("Cerebro Skill 元数据缺少名称或描述")
        if not isinstance(permissions, list) or not all(
            isinstance(permission, str) for permission in permissions
        ):
            raise ValueError("Cerebro Skill 权限元数据无效")
        return cls.from_code(
            source_code,
            name=name,
            description=description,
            required_permissions=permissions,
            source_path=resolved,
        )

    @abstractmethod
    def execute(self, params: dict[str, Any], context: AgentContext) -> SkillResult:
        """Execute the skill using only capabilities exposed by ``context``."""
