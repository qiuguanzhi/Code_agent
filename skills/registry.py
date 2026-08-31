"""Skill discovery, enablement, permission checks, and dispatch."""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import re
from collections.abc import Callable, Iterable
from typing import Any

from skills.base import AgentContext, Skill, SkillResult


SkillConfirmation = Callable[[Skill], bool]
_VALID_NAME = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


class SkillRegistry:
    """Own all discovered skills and their per-run security policy."""

    def __init__(
        self,
        *,
        granted_permissions: Iterable[str] = ("filesystem",),
        enabled_names: Iterable[str] | None = None,
        confirm_high_risk: SkillConfirmation | None = None,
    ) -> None:
        self._skills: dict[str, Skill] = {}
        self._granted_permissions = frozenset(granted_permissions)
        self._enabled_names = None if enabled_names is None else set(enabled_names)
        self._confirm_high_risk = confirm_high_risk

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

    def register(self, skill_class: type[Skill]) -> Skill:
        """Instantiate and register one validated Skill class."""

        if not inspect.isclass(skill_class) or not issubclass(skill_class, Skill):
            raise TypeError("skill_class must inherit Skill")
        skill = skill_class()
        if not _VALID_NAME.fullmatch(skill.name):
            raise ValueError(f"invalid skill name: {skill.name!r}")
        if not skill.description.strip():
            raise ValueError(f"skill {skill.name!r} requires a description")
        if skill.name in self._skills:
            raise ValueError(f"duplicate skill: {skill.name}")
        self._skills[skill.name] = skill
        return skill

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
