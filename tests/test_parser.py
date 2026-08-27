"""Tests for strict tool-call parsing and repeat detection."""

from typing import Any

import pytest

from agent.parser import (
    RepeatedToolCallError,
    ToolArgumentsError,
    canonical_tool_signature,
    extract_goal_plan,
    parse_tool_arguments,
    register_call_signature,
)
from agent.state import AgentState, ToolCall


def _call(name: str, arguments_json: str, call_id: str = "call-1") -> ToolCall:
    """Build a normalized call for parser tests."""

    return ToolCall(id=call_id, name=name, arguments_json=arguments_json)


def test_parse_tool_arguments_accepts_valid_schema() -> None:
    call = _call(
        "read_file",
        '{"path":"src/main.py","start_line":1,"max_lines":20,"max_chars":2000}',
    )

    result = parse_tool_arguments(call)

    assert result == {
        "path": "src/main.py",
        "start_line": 1,
        "max_lines": 20,
        "max_chars": 2_000,
    }


@pytest.mark.parametrize(
    ("arguments_json", "expected_code"),
    [
        ("{bad json", "invalid_json"),
        ("[]", "arguments_must_be_object"),
        ('{"path":"x.py"}', "missing_arguments"),
        (
            '{"path":"x.py","start_line":1,"max_lines":20,"max_chars":2000,"extra":true}',
            "unexpected_arguments",
        ),
        (
            '{"path":"x.py","start_line":true,"max_lines":20,"max_chars":2000}',
            "invalid_argument_type",
        ),
        (
            '{"path":"x.py","start_line":0,"max_lines":20,"max_chars":2000}',
            "argument_out_of_range",
        ),
    ],
)
def test_parse_tool_arguments_rejects_invalid_input(
    arguments_json: str,
    expected_code: str,
) -> None:
    with pytest.raises(ToolArgumentsError) as captured:
        parse_tool_arguments(_call("read_file", arguments_json))

    assert captured.value.code == expected_code


def test_parse_tool_arguments_rejects_unknown_tool() -> None:
    with pytest.raises(ToolArgumentsError) as captured:
        parse_tool_arguments(_call("delete_everything", "{}"))

    assert captured.value.code == "unknown_tool"
    assert "read_file" in captured.value.details["allowed"]


def test_canonical_signature_ignores_object_key_order() -> None:
    first: dict[str, Any] = {"path": "a.py", "content": "x", "expected_sha256": "hash"}
    second: dict[str, Any] = {"expected_sha256": "hash", "content": "x", "path": "a.py"}

    assert canonical_tool_signature("write_file", first) == canonical_tool_signature(
        "write_file", second
    )


def test_register_call_signature_rejects_calls_beyond_limit() -> None:
    state = AgentState()
    arguments = {"path": "a.py"}

    for _ in range(3):
        register_call_signature(state, "read_file", arguments, max_repeats=3)

    with pytest.raises(RepeatedToolCallError) as captured:
        register_call_signature(state, "read_file", arguments, max_repeats=3)

    assert captured.value.count == 4


def test_agent_state_supports_auto_and_goal_modes() -> None:
    assert AgentState().mode == "auto"
    assert AgentState(mode="goal").mode == "goal"

    with pytest.raises(ValueError, match="mode must be one of"):
        AgentState(mode="unsupported")


def test_extract_goal_plan_is_non_disruptive_stub() -> None:
    assert extract_goal_plan("1. Inspect files\n2. Run tests") == []

