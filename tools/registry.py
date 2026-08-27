"""Validated tool dispatch with extension hooks for write confirmation."""

from __future__ import annotations

import json
from collections.abc import Mapping
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


class ToolRegistry:
    """Bind model-visible tool calls to local workspace-scoped functions."""

    def __init__(
        self,
        workspace: Path,
        *,
        write_policy: Mapping[str, bool] | None = None,
        max_same_call: int = 3,
    ) -> None:
        self.workspace = workspace.resolve(strict=True)
        self.write_policy = dict(WRITE_POLICY if write_policy is None else write_policy)
        self.max_same_call = max_same_call
        self.schemas = get_tool_schemas()

    def _confirm_write(self, path: str) -> bool:
        """Placeholder confirmation hook for file modifications."""

        _ = path
        # TODO: 接入用户交互界面
        return True

    def execute_one_call(self, call: ToolCall, state: AgentState) -> str:
        """Validate, deduplicate, confirm, and execute one local tool call."""

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
                result = run_command(self.workspace, **arguments)
            else:
                result = tool_error("unknown_tool", f"unknown tool: {call.name}")
        except Exception as exc:
            result = tool_error("tool_execution_error", f"{type(exc).__name__}: {exc}")

        if call.name == "write_file":
            result.setdefault("meta", {})["confirmed_by_user"] = bool(confirmed_by_user)
            if result.get("ok") is True:
                file_path = str(arguments["path"])
                new_hash = result["meta"].get("sha256")
                if isinstance(new_hash, str):
                    state.changed_files[file_path] = new_hash

        return self._cache_result(call.id, result, state)

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

