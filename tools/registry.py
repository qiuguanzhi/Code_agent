"""Validated tool dispatch with extension hooks for write confirmation."""

from __future__ import annotations

import json
from collections.abc import Callable, Collection, Mapping
from pathlib import Path
from typing import Any

from agent.parser import (
    RepeatedToolCallError,
    ToolArgumentsError,
    parse_tool_arguments,
    register_call_signature,
    tool_error,
)
from agent.state import AgentState, ToolCall
from tools.filesystem import (
    delete_file,
    read_file,
    read_staged_file,
    resolve_in_workspace,
    stage_write_file,
    write_file,
)
from tools.schemas import get_tool_schemas
from tools.shell import run_command
from skills.base import AgentContext
from skills.registry import SkillRegistry


WRITE_POLICY: dict[str, bool] = {"require_confirmation": True}


class ToolStopRequested(RuntimeError):
    """Raised at a tool boundary after cooperative cancellation."""


class ToolRegistry:
    """Bind model-visible tool calls to local workspace-scoped functions."""

    def __init__(
        self,
        workspace: Path,
        *,
        write_policy: Mapping[str, bool] | None = None,
        max_same_call: int = 3,
        confirm_write: Callable[[str], bool] | None = None,
        should_stop: Callable[[], bool] | None = None,
        defer_writes: bool = False,
        forbidden_tools: Collection[str] | None = None,
        skill_registry: SkillRegistry | None = None,
        on_skill_update: Callable[[str, str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.workspace = workspace.resolve(strict=True)
        self.write_policy = dict(WRITE_POLICY if write_policy is None else write_policy)
        self.max_same_call = max_same_call
        self.confirm_write = confirm_write
        self.should_stop = should_stop
        self.defer_writes = defer_writes
        self.forbidden_tools = frozenset(forbidden_tools or ())
        self.skill_registry = skill_registry
        self.on_skill_update = on_skill_update
        self._native_schemas = get_tool_schemas()
        self.schemas = list(self._native_schemas)
        if self.skill_registry is not None:
            skill_schemas = self.skill_registry.get_skills_as_tools()
            native_names = {
                str(schema.get("function", {}).get("name", ""))
                for schema in self._native_schemas
            }
            collisions = sorted(
                str(schema.get("function", {}).get("name", ""))
                for schema in skill_schemas
                if str(schema.get("function", {}).get("name", "")) in native_names
            )
            if collisions:
                raise ValueError(
                    "skill names cannot shadow native tools: " + ", ".join(collisions)
                )
            self.schemas.extend(skill_schemas)

    def _confirm_write(self, path: str) -> bool:
        """Placeholder confirmation hook for file modifications."""

        # TODO: 接入用户交互界面
        if self.confirm_write is None:
            return True
        return self.confirm_write(path)

    def execute_one_call(self, call: ToolCall, state: AgentState) -> str:
        """Validate, deduplicate, confirm, and execute one local tool call."""

        self._raise_if_stopped()
        if call.id in state.tool_result_cache:
            return state.tool_result_cache[call.id]

        try:
            arguments = parse_tool_arguments(call, self.schemas)
            register_call_signature(
                state,
                call.name,
                arguments,
                max_repeats=self.max_same_call,
            )
        except ToolArgumentsError as exc:
            result = tool_error(exc.code, str(exc), details=exc.details)
            return self._cache_result(call.id, result, state)
        except RepeatedToolCallError as exc:
            result = tool_error(
                "repeated_call_limit",
                str(exc),
                details={"signature": exc.signature, "count": exc.count},
            )
            return self._cache_result(call.id, result, state)

        if call.name in self.forbidden_tools:
            result = tool_error(
                "task_scope_violation",
                f"tool {call.name} conflicts with an explicit user constraint",
                details={"tool": call.name},
            )
            return self._cache_result(call.id, result, state)

        confirmed_by_user: bool | None = None
        self._raise_if_stopped()
        if call.name == "write_file" and self.write_policy.get("require_confirmation", True):
            confirmed_by_user = self._confirm_write(str(arguments["path"]))
            if not confirmed_by_user:
                result = tool_error(
                    "user_aborted",
                    "user rejected the file modification",
                    meta={"confirmed_by_user": False},
                )
                return self._cache_result(call.id, result, state)

        try:
            skill = (
                self.skill_registry.get_skill(call.name)
                if self.skill_registry is not None
                else None
            )
            if skill is not None:
                context = AgentContext(
                    workspace=self.workspace,
                    execute_tool=lambda name, params: self._execute_native_for_skill(
                        name,
                        params,
                        state,
                        skill_permissions=skill.required_permissions,
                        high_risk=skill.high_risk,
                    ),
                    emit_update=self.on_skill_update,
                )
                result = self.skill_registry.execute(call.name, arguments, context)
            elif call.name == "read_file":
                staged = self._pending_write_for_path(
                    state,
                    str(arguments["path"]),
                )
                if staged is not None:
                    result = read_staged_file(
                        path=str(arguments["path"]),
                        content=str(staged["content"]),
                        start_line=int(arguments["start_line"]),
                        max_lines=int(arguments["max_lines"]),
                        max_chars=int(arguments["max_chars"]),
                    )
                else:
                    result = read_file(self.workspace, **arguments)
            elif call.name == "write_file":
                if self.defer_writes:
                    result = stage_write_file(
                        self.workspace,
                        pending_writes=state.pending_writes,
                        **arguments,
                    )
                    if result.get("ok") is True:
                        staged_path = str(result.get("meta", {}).get("path", ""))
                        staged_entry = self._pending_write_for_path(state, staged_path)
                        if staged_entry is not None:
                            staged_entry["step"] = state.step
                else:
                    result = write_file(self.workspace, **arguments)
            elif call.name == "delete_file":
                if self.defer_writes and state.pending_writes:
                    result = tool_error(
                        "pending_writes_require_confirmation",
                        "confirm or reject staged writes before deleting files",
                    )
                else:
                    result = delete_file(self.workspace, **arguments)
            elif call.name == "run_command":
                if self.defer_writes and state.pending_writes:
                    result = tool_error(
                        "pending_writes_require_confirmation",
                        "staged writes are not on disk yet; finish the plan and wait for batch approval",
                        meta={"pending_count": len(state.pending_writes)},
                    )
                else:
                    result = run_command(
                        self.workspace,
                        **arguments,
                        should_stop=self.should_stop,
                    )
            else:
                result = tool_error("unknown_tool", f"unknown tool: {call.name}")
        except Exception as exc:
            result = tool_error("tool_execution_error", f"{type(exc).__name__}: {exc}")

        if call.name == "write_file":
            meta = result.setdefault("meta", {})
            meta["confirmed_by_user"] = (
                None if meta.get("staged") is True else bool(confirmed_by_user)
            )
            if (
                result.get("ok") is True
                and result.get("meta", {}).get("staged") is not True
            ):
                file_path = str(arguments["path"])
                new_hash = result["meta"].get("sha256")
                diff_text = result["meta"].get("diff")
                if isinstance(new_hash, str):
                    state.changed_file_hashes[file_path] = new_hash
                if isinstance(diff_text, str):
                    state.changed_files[file_path] = diff_text

        self._raise_if_stopped()
        return self._cache_result(call.id, result, state)

    def _execute_native_for_skill(
        self,
        name: str,
        params: dict[str, Any],
        state: AgentState,
        *,
        skill_permissions: Collection[str],
        high_risk: bool,
    ) -> dict[str, Any]:
        """Validate a nested native call and enforce capability boundaries."""

        if name in self.forbidden_tools:
            return tool_error(
                "task_scope_violation",
                f"tool {name} conflicts with an explicit user constraint",
            )
        required_permission = {
            "read_file": "filesystem",
            "write_file": "filesystem_write",
            "delete_file": "filesystem_write",
            "run_command": "process",
        }.get(name)
        if required_permission is None:
            return tool_error("unknown_tool", f"unknown native tool: {name}")
        if required_permission not in skill_permissions:
            return tool_error(
                "skill_capability_denied",
                f"skill did not declare permission: {required_permission}",
            )
        if name in {"write_file", "delete_file"} and not high_risk:
            return tool_error(
                "skill_capability_denied",
                "filesystem mutation requires a high-risk skill",
            )
        nested = ToolCall(
            id=f"skill:{name}:{len(state.tool_result_cache)}",
            name=name,
            arguments_json=json.dumps(params, ensure_ascii=False),
        )
        try:
            arguments = parse_tool_arguments(nested, self._native_schemas)
        except ToolArgumentsError as exc:
            return tool_error(exc.code, str(exc), details=exc.details)

        if name == "read_file":
            staged = self._pending_write_for_path(state, str(arguments["path"]))
            if staged is not None:
                return read_staged_file(
                    path=str(arguments["path"]),
                    content=str(staged["content"]),
                    start_line=int(arguments["start_line"]),
                    max_lines=int(arguments["max_lines"]),
                    max_chars=int(arguments["max_chars"]),
                )
            return read_file(self.workspace, **arguments)
        if name == "run_command":
            if self.defer_writes and state.pending_writes:
                return tool_error(
                    "pending_writes_require_confirmation",
                    "staged writes are not on disk yet",
                )
            return run_command(
                self.workspace,
                **arguments,
                should_stop=self.should_stop,
            )
        if name == "write_file":
            if self.defer_writes:
                result = stage_write_file(
                    self.workspace,
                    pending_writes=state.pending_writes,
                    **arguments,
                )
                if result.get("ok") is True:
                    staged_path = str(result.get("meta", {}).get("path", ""))
                    staged_entry = self._pending_write_for_path(state, staged_path)
                    if staged_entry is not None:
                        staged_entry["step"] = state.step
                return result
            if (
                self.write_policy.get("require_confirmation", True)
                and not self._confirm_write(str(arguments["path"]))
            ):
                return tool_error("user_aborted", "user rejected skill write")
            result = write_file(self.workspace, **arguments)
            if result.get("ok") is True:
                meta = result.get("meta", {})
                path = str(arguments["path"])
                if isinstance(meta.get("sha256"), str):
                    state.changed_file_hashes[path] = meta["sha256"]
                if isinstance(meta.get("diff"), str):
                    state.changed_files[path] = meta["diff"]
            return result
        if self.defer_writes and state.pending_writes:
            return tool_error(
                "pending_writes_require_confirmation",
                "confirm or reject staged writes before deleting files",
            )
        return delete_file(self.workspace, **arguments)

    def _pending_write_for_path(
        self,
        state: AgentState,
        path: str,
    ) -> dict[str, Any] | None:
        """Return the latest in-memory version for one staged relative path."""

        if not self.defer_writes:
            return None
        try:
            normalized = (
                resolve_in_workspace(self.workspace, path, must_exist=False)
                .relative_to(self.workspace)
                .as_posix()
            )
        except (OSError, RuntimeError, ValueError):
            return None
        for item in reversed(state.pending_writes):
            if str(item.get("path", "")) == normalized:
                return item
        return None

    def _raise_if_stopped(self) -> None:
        """Abort immediately when the owning Agent requested cancellation."""

        if self.should_stop is not None and self.should_stop():
            raise ToolStopRequested("user requested stop")

    def _cache_result(
        self,
        call_id: str,
        result: dict[str, Any],
        state: AgentState,
    ) -> str:
        """Encode and cache a tool result to make call-id retries idempotent."""

        encoded = json.dumps(result, ensure_ascii=False, sort_keys=True)
        state.tool_result_cache[call_id] = encoded
        return encoded
