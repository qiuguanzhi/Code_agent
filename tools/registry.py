"""Validated tool dispatch with extension hooks for write confirmation."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
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
from tools.filesystem import read_file, write_file
from tools.schemas import get_tool_schemas
from tools.shell import run_command


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
    ) -> None:
        self.workspace = workspace.resolve(strict=True)
        self.write_policy = dict(WRITE_POLICY if write_policy is None else write_policy)
        self.max_same_call = max_same_call
        self.confirm_write = confirm_write
        self.should_stop = should_stop
        self.schemas = get_tool_schemas()

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
            if call.name == "read_file":
                result = read_file(self.workspace, **arguments)
            elif call.name == "write_file":
                result = write_file(self.workspace, **arguments)
            elif call.name == "run_command":
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
            result.setdefault("meta", {})["confirmed_by_user"] = bool(confirmed_by_user)
            if result.get("ok") is True:
                file_path = str(arguments["path"])
                new_hash = result["meta"].get("sha256")
                diff_text = result["meta"].get("diff")
                if isinstance(new_hash, str):
                    state.changed_file_hashes[file_path] = new_hash
                if isinstance(diff_text, str):
                    state.changed_files[file_path] = diff_text

        self._raise_if_stopped()
        return self._cache_result(call.id, result, state)

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
