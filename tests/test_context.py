"""Tests for protocol-safe context fitting and deterministic work memory."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from agent.context import (
    ContextBudgetError,
    build_work_memory,
    estimate_tokens,
    fit_context,
    group_protocol_units,
)


def _tool_call(call_id: str, name: str = "read_file") -> dict[str, Any]:
    """Build one protocol-format tool call."""

    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": "{}"},
    }


def _assert_no_split_tool_units(messages: Sequence[Mapping[str, Any]]) -> None:
    """Assert every retained assistant call has its complete tool-result set."""

    for index, message in enumerate(messages):
        calls = message.get("tool_calls")
        if message.get("role") != "assistant" or not isinstance(calls, list):
            continue
        expected = {call["id"] for call in calls}
        observed: set[str] = set()
        cursor = index + 1
        while cursor < len(messages) and messages[cursor].get("role") == "tool":
            observed.add(str(messages[cursor].get("tool_call_id")))
            cursor += 1
        assert observed == expected


def test_estimate_tokens_adds_twenty_percent_margin() -> None:
    assert estimate_tokens("12345") == math.ceil(5 * 1.20)


def test_group_protocol_units_keeps_parallel_tool_results_together() -> None:
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "task"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [_tool_call("call-1"), _tool_call("call-2")],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": '{"ok":true}'},
        {"role": "tool", "tool_call_id": "call-2", "content": '{"ok":true}'},
        {"role": "assistant", "content": "done"},
    ]

    units = group_protocol_units(messages)

    assert len(units) == 4
    assert [message["role"] for message in units[2]] == ["assistant", "tool", "tool"]


def test_build_work_memory_creates_structured_json_facts() -> None:
    dropped = [
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [_tool_call("call-1", "run_command")],
            },
            {
                "role": "tool",
                "tool_call_id": "call-1",
                "content": json.dumps(
                    {
                        "ok": False,
                        "error": {"code": "nonzero_exit"},
                        "meta": {"exit_code": 1},
                    }
                ),
            },
        ]
    ]

    memory = build_work_memory(dropped)

    assert memory is not None
    payload = json.loads(memory["content"].split("\n", maxsplit=1)[1])
    assert payload["type"] == "work_memory"
    assert payload["facts"][0]["tool"] == "run_command"
    assert payload["facts"][0]["error_code"] == "nonzero_exit"


def test_fit_context_compresses_old_history_without_splitting_protocol() -> None:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "system rules"},
        {"role": "user", "content": "repair the project"},
    ]
    for index in range(6):
        call_id = f"call-{index}"
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [_tool_call(call_id)],
                },
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps(
                        {
                            "ok": True,
                            "data": f"large-output-{index}-" + "x" * 1_000,
                            "meta": {"path": f"file-{index}.py", "sha256": str(index)},
                        }
                    ),
                },
            ]
        )

    fitted = fit_context(messages, [], input_budget=2_500)

    assert estimate_tokens(fitted) <= 2_500
    assert any(
        isinstance(message.get("content"), str)
        and message["content"].startswith("WORK_MEMORY_JSON")
        for message in fitted
    )
    assert any(message.get("role") == "user" for message in fitted)
    _assert_no_split_tool_units(fitted)


def test_fit_context_rejects_impossibly_small_budget() -> None:
    messages = [
        {"role": "system", "content": "s" * 500},
        {"role": "user", "content": "u" * 500},
    ]

    with pytest.raises(ContextBudgetError):
        fit_context(messages, [{"schema": "x" * 500}], input_budget=10)

