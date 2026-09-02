"""Skill discovery, enablement, permission checks, and dispatch."""

from __future__ import annotations

import ast
import importlib
import inspect
import json
import os
import pkgutil
import re
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from skills.base import (
    SKILL_METADATA_PREFIX,
    AgentContext,
    Skill,
    SkillResult,
)


SkillConfirmation = Callable[[Skill], bool]
_VALID_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_NATIVE_TOOL_NAMES = frozenset(
    {"read_file", "write_file", "delete_file", "run_command"}
)
_KNOWN_PERMISSIONS = frozenset(
    {"filesystem", "filesystem_write", "network", "process"}
)
DEFAULT_USER_SKILL_DIR = Path(__file__).resolve().parent / "user"
_DANGEROUS_CALLS = frozenset(
    {
        "__import__",
        "breakpoint",
        "compile",
        "eval",
        "exec",
        "getattr",
        "globals",
        "input",
        "locals",
        "open",
        "setattr",
        "vars",
    }
)


class SkillCodeSafetyError(ValueError):
    """Signal that manually entered code needs explicit user acknowledgement."""

    def __init__(self, warnings: list[str]) -> None:
        """Retain structured warnings for the GUI confirmation dialog."""

        self.warnings = tuple(warnings)
        super().__init__("；".join(warnings))


def inspect_skill_code(source_code: str) -> list[str]:
    """Return auditable warnings without executing user-entered Python."""

    try:
        tree = ast.parse(source_code, filename="<user-skill>", mode="exec")
    except SyntaxError as exc:
        line = exc.lineno or 0
        raise ValueError(f"Skill 代码语法错误（第 {line} 行）：{exc.msg}") from exc
    warnings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise ValueError(
                "动态 Skill 禁止 import；请通过 context.call_tool() 使用已授权能力"
            )
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise ValueError("动态 Skill 禁止访问双下划线内部属性")
        if isinstance(node, ast.Name) and node.id == "__builtins__":
            raise ValueError("动态 Skill 禁止访问 __builtins__")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in _DANGEROUS_CALLS:
                warning = f"检测到危险函数 {node.func.id}()（第 {node.lineno} 行）"
                if warning not in warnings:
                    warnings.append(warning)
    return warnings


class SkillRegistry:
    """Own all discovered skills and their per-run security policy."""

    def __init__(
        self,
        *,
        granted_permissions: Iterable[str] = ("filesystem",),
        enabled_names: Iterable[str] | None = None,
        confirm_high_risk: SkillConfirmation | None = None,
        user_skill_dir: Path | str | None = None,
    ) -> None:
        self._skills: dict[str, Skill] = {}
        self._granted_permissions = frozenset(granted_permissions)
        self._enabled_names = None if enabled_names is None else set(enabled_names)
        self._confirm_high_risk = confirm_high_risk
        self.user_skill_dir = Path(
            user_skill_dir if user_skill_dir is not None else DEFAULT_USER_SKILL_DIR
        ).resolve()

    @classmethod
    def discover_builtin(
        cls,
        *,
        granted_permissions: Iterable[str] = ("filesystem",),
        enabled_names: Iterable[str] | None = None,
        confirm_high_risk: SkillConfirmation | None = None,
    ) -> "SkillRegistry":
        """Discover concrete Skill subclasses from ``skills.builtin``."""

        registry = cls(
            granted_permissions=granted_permissions,
            enabled_names=enabled_names,
            confirm_high_risk=confirm_high_risk,
        )
        package = importlib.import_module("skills.builtin")
        modules = [package]
        package_path = getattr(package, "__path__", None)
        if package_path is not None:
            for module_info in pkgutil.iter_modules(package_path, package.__name__ + "."):
                modules.append(importlib.import_module(module_info.name))
        for module in modules:
            for _, candidate in inspect.getmembers(module, inspect.isclass):
                if candidate is Skill or not issubclass(candidate, Skill):
                    continue
                if candidate.__module__ != module.__name__:
                    continue
                registry.register(candidate)
        return registry

    @classmethod
    def discover_all(
        cls,
        *,
        granted_permissions: Iterable[str] = ("filesystem",),
        enabled_names: Iterable[str] | None = None,
        confirm_high_risk: SkillConfirmation | None = None,
        user_skill_dir: Path | str | None = None,
    ) -> "SkillRegistry":
        """Discover built-in Skills plus valid files in the controlled user directory."""

        registry = cls(
            granted_permissions=granted_permissions,
            enabled_names=enabled_names,
            confirm_high_risk=confirm_high_risk,
            user_skill_dir=user_skill_dir,
        )
        builtin = cls.discover_builtin(
            granted_permissions=granted_permissions,
            enabled_names=enabled_names,
            confirm_high_risk=confirm_high_risk,
        )
        for skill in builtin.list_skills():
            registry._register_instance(skill)
        if registry.user_skill_dir.exists():
            for source in sorted(registry.user_skill_dir.glob("*.py")):
                if source.name == "__init__.py":
                    continue
                try:
                    skill = Skill.from_file(source)
                    if skill.name in _NATIVE_TOOL_NAMES:
                        raise ValueError(
                            f"用户 Skill 不可覆盖原生工具: {skill.name}"
                        )
                    skill.source_path = source.resolve()
                    registry._register_instance(skill)
                except (OSError, TypeError, ValueError) as exc:
                    print(
                        "[Cerebro::Skill] "
                        f"跳过无效用户 Skill {source.name}: {exc}"
                    )
        return registry

    def register(self, skill_class: type[Skill]) -> Skill:
        """Instantiate and register one validated Skill class."""

        if not inspect.isclass(skill_class) or not issubclass(skill_class, Skill):
            raise TypeError("skill_class must inherit Skill")
        skill = skill_class()
        return self._register_instance(skill)

    def _register_instance(self, skill: Skill) -> Skill:
        """Validate and store one already-instantiated Skill."""

        if not isinstance(skill, Skill):
            raise TypeError("skill must inherit Skill")
        self._validate_new_skill(skill)
        self._skills[skill.name] = skill
        return skill

    def register_from_code(
        self,
        *,
        name: str,
        description: str,
        source_code: str,
        required_permissions: Iterable[str] = (),
        allow_dangerous: bool = False,
    ) -> Skill:
        """Validate, restrict, register, and atomically persist a form-defined Skill."""

        normalized_name = name.strip()
        normalized_description = description.strip()
        permissions = frozenset(required_permissions)
        if not _VALID_NAME.fullmatch(normalized_name):
            raise ValueError("Skill 名称须以小写字母开头，仅含小写字母、数字和下划线")
        if not normalized_description:
            raise ValueError("Skill 描述不能为空")
        if normalized_name in _NATIVE_TOOL_NAMES:
            raise ValueError(f"用户 Skill 不可覆盖原生工具: {normalized_name}")
        if normalized_name in self._skills:
            raise ValueError(f"duplicate skill: {normalized_name}")
        unknown_permissions = sorted(permissions - _KNOWN_PERMISSIONS)
        if unknown_permissions:
            raise ValueError(
                "未知 Skill 权限：" + ", ".join(unknown_permissions)
            )
        warnings = inspect_skill_code(source_code)
        if warnings and not allow_dangerous:
            raise SkillCodeSafetyError(warnings)

        skill = Skill.from_code(
            source_code,
            name=normalized_name,
            description=normalized_description,
            required_permissions=permissions,
        )
        self._validate_new_skill(skill)
        self.user_skill_dir.mkdir(parents=True, exist_ok=True)
        target = (self.user_skill_dir / f"skill_{normalized_name}.py").resolve()
        try:
            target.relative_to(self.user_skill_dir)
        except ValueError as exc:
            raise ValueError("用户 Skill 目标路径逃逸") from exc
        if target.exists():
            raise ValueError(f"用户 Skill 文件已存在: {target.name}")
        metadata = json.dumps(
            {
                "name": normalized_name,
                "description": normalized_description,
                "permissions": sorted(permissions),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        persisted_source = f"{SKILL_METADATA_PREFIX}{metadata}\n{source_code.rstrip()}\n"
        temporary = target.with_name(f".{target.name}.tmp")
        try:
            temporary.write_text(persisted_source, encoding="utf-8")
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()
        skill.source_path = target
        self._skills[skill.name] = skill
        if self._enabled_names is not None:
            self._enabled_names.add(skill.name)
        return skill

    def unregister(self, name: str) -> Skill:
        """Remove one user Skill and its controlled source file."""

        skill = self._skills.get(name)
        if skill is None:
            raise KeyError(name)
        if skill.built_in:
            raise ValueError("内置 Skill 不可删除")
        source = skill.source_path or self.user_skill_dir / f"skill_{skill.name}.py"
        resolved_source = source.resolve()
        try:
            resolved_source.relative_to(self.user_skill_dir)
        except ValueError as exc:
            raise ValueError("拒绝删除用户 Skill 目录之外的文件") from exc
        if resolved_source.exists():
            if not resolved_source.is_file():
                raise ValueError("用户 Skill 源路径不是普通文件")
            resolved_source.unlink()
        removed = self._skills.pop(name)
        if self._enabled_names is not None:
            self._enabled_names.discard(name)
        return removed

    def _validate_new_skill(self, skill: Skill) -> None:
        """Validate a candidate without mutating registry state."""

        if not isinstance(skill.name, str) or not _VALID_NAME.fullmatch(skill.name):
            raise ValueError(f"invalid skill name: {skill.name!r}")
        if not isinstance(skill.description, str) or not skill.description.strip():
            raise ValueError(f"skill {skill.name!r} requires a description")
        if not isinstance(skill.parameters_schema, dict):
            raise ValueError(f"skill {skill.name!r} requires a JSON object schema")
        if skill.parameters_schema.get("type") != "object":
            raise ValueError(f"skill {skill.name!r} parameters schema must be an object")
        if not isinstance(skill.required_permissions, frozenset) or not all(
            isinstance(permission, str) and permission
            for permission in skill.required_permissions
        ):
            raise ValueError(
                f"skill {skill.name!r} required_permissions must be frozenset[str]"
            )
        unknown_permissions = sorted(skill.required_permissions - _KNOWN_PERMISSIONS)
        if unknown_permissions:
            raise ValueError(
                f"skill {skill.name!r} declares unknown permissions: "
                + ", ".join(unknown_permissions)
            )
        if not isinstance(skill.high_risk, bool):
            raise ValueError(f"skill {skill.name!r} high_risk must be boolean")
        if skill.name in self._skills:
            raise ValueError(f"duplicate skill: {skill.name}")

    def get_skill(self, name: str) -> Skill | None:
        """Return a registered skill, including a disabled one."""

        return self._skills.get(name)

    def list_skills(self) -> list[Skill]:
        """Return all registered skills in deterministic name order."""

        return [self._skills[name] for name in sorted(self._skills)]

    def is_enabled(self, name: str) -> bool:
        """Return whether a registered skill is exposed to the model."""

        return name in self._skills and (
            self._enabled_names is None or name in self._enabled_names
        )

    def set_enabled(self, name: str, enabled: bool) -> None:
        """Change enablement while preserving an explicit policy set."""

        if name not in self._skills:
            raise KeyError(name)
        if self._enabled_names is None:
            self._enabled_names = set(self._skills)
        if enabled:
            self._enabled_names.add(name)
        else:
            self._enabled_names.discard(name)

    def enabled_names(self) -> frozenset[str]:
        """Return the currently model-visible skill names."""

        return frozenset(
            skill.name for skill in self.list_skills() if self.is_enabled(skill.name)
        )

    def get_skills_as_tools(self) -> list[dict[str, Any]]:
        """Translate enabled skills to native OpenAI tool schemas."""

        return [
            {
                "type": "function",
                "function": {
                    "name": skill.name,
                    "description": (
                        f"Skill: {skill.description} Required permissions: "
                        f"{', '.join(sorted(skill.required_permissions)) or 'none'}."
                    ),
                    "parameters": skill.parameters_schema,
                },
            }
            for skill in self.list_skills()
            if self.is_enabled(skill.name)
        ]

    def execute(
        self,
        name: str,
        params: dict[str, Any],
        context: AgentContext,
    ) -> dict[str, Any]:
        """Execute one enabled, authorized skill and return ToolResult JSON."""

        skill = self._skills.get(name)
        if skill is None:
            return self._error("unknown_skill", f"unknown skill: {name}")
        if not self.is_enabled(name):
            return self._error("skill_disabled", f"skill is disabled: {name}")
        missing = sorted(skill.required_permissions - self._granted_permissions)
        if missing:
            return self._error(
                "skill_permission_denied",
                f"skill requires permissions that were not granted: {', '.join(missing)}",
                details={"missing_permissions": missing},
            )
        if skill.high_risk and (
            self._confirm_high_risk is None or not self._confirm_high_risk(skill)
        ):
            return self._error(
                "skill_confirmation_required",
                f"high-risk skill was not approved: {name}",
            )
        context.log("skill_started", f"技能开始：{name}", skill=name)
        try:
            result = skill.execute(dict(params), context)
        except Exception as exc:
            return self._error(
                "skill_execution_error",
                f"{type(exc).__name__}: {exc}",
            )
        if not isinstance(result, SkillResult):
            return self._error(
                "invalid_skill_result",
                f"skill {name} did not return SkillResult",
            )
        context.log("skill_completed", f"技能完成：{name}", skill=name, ok=result.ok)
        return result.as_tool_result()

    @staticmethod
    def _error(
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build the shared ToolResult error shape without importing tools."""

        return {
            "ok": False,
            "data": None,
            "error": {"code": code, "message": message, "details": details or {}},
            "meta": {"kind": "skill"},
        }
