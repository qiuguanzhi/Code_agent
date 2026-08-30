"""End-to-end tests for the autonomous loop using a deterministic provider."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from agent.loop import AgentConfig, run_agent
from agent.state import AssistantTurn, ToolCall
from providers.base import ModelProvider
from tools.filesystem import sha256_file_streaming


class FakeProvider(ModelProvider):
    """Return predefined turns or raise predefined exceptions in order."""

    def __init__(self, actions: Sequence[AssistantTurn | Exception]) -> None:
        self.actions = list(actions)
        self.calls = 0
        self.requests: list[list[dict[str, Any]]] = []

    def complete(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
    ) -> AssistantTurn:
        """Consume the next action without network access."""

        _ = tools
        self.requests.append([dict(message) for message in messages])
        self.calls += 1
        action = self.actions.pop(0)
        if isinstance(action, Exception):
            raise action
        return action


def _tool_turn(call_id: str, name: str, arguments: dict[str, Any]) -> AssistantTurn:
    """Build a provider-normalized assistant tool-call turn."""

    arguments_json = json.dumps(arguments)
    call = ToolCall(id=call_id, name=name, arguments_json=arguments_json)
    return AssistantTurn(
        content=None,
        tool_calls=[call],
        protocol_message={
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": arguments_json},
                }
            ],
        },
        finish_reason="tool_calls",
    )


def _final_turn(content: str = "Task completed and tests pass.") -> AssistantTurn:
    """Build a provider-normalized final text turn."""

    return AssistantTurn(
        content=content,
        tool_calls=[],
        protocol_message={"role": "assistant", "content": content},
        finish_reason="stop",
    )


def _create_buggy_workspace(tmp_path: Path) -> tuple[Path, Path]:
    """Create a tiny project whose zero-division behavior needs repair."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    calculator = workspace / "calc.py"
    calculator.write_text(
        "def divide(a: float, b: float) -> float:\n    return a / b\n",
        encoding="utf-8",
        newline="\n",
    )
    (workspace / "test_calc.py").write_text(
        "import pytest\n"
        "from calc import divide\n\n"
        "def test_zero() -> None:\n"
        "    with pytest.raises(ValueError, match='denominator cannot be zero'):\n"
        "        divide(1, 0)\n",
        encoding="utf-8",
        newline="\n",
    )
    (workspace / "pytest.ini").write_text(
        "[pytest]\ntestpaths = .\n",
        encoding="utf-8",
        newline="\n",
    )
    return workspace, calculator


def test_run_agent_completes_read_write_run_finish_loop(tmp_path: Path) -> None:
    workspace, calculator = _create_buggy_workspace(tmp_path)
    original_hash = sha256_file_streaming(calculator)
    repaired_content = (
        "def divide(a: float, b: float) -> float:\n"
        "    if b == 0:\n"
        "        raise ValueError('denominator cannot be zero')\n"
        "    return a / b\n"
    )
    provider = FakeProvider(
        [
            _tool_turn(
                "read-1",
                "read_file",
                {"path": "calc.py", "start_line": 1, "max_lines": 100, "max_chars": 10_000},
            ),
            _tool_turn(
                "write-1",
                "write_file",
                {
                    "path": "calc.py",
                    "content": repaired_content,
                    "expected_sha256": original_hash,
                },
            ),
            _tool_turn(
                "run-1",
                "run_command",
                {
                    "argv": [sys.executable, "-m", "pytest", "-q"],
                    "cwd": ".",
                    "timeout_seconds": 30,
                    "max_output_chars": 10_000,
                },
            ),
            _final_turn(),
        ]
    )
    events: list[dict[str, Any]] = []
    cfg = AgentConfig(workspace=workspace, provider=provider, max_steps=8)

    result = run_agent("repair zero division and run tests", cfg, events.append)

    assert result.status == "completed"
    assert result.reason == "final_answer"
    assert calculator.read_text(encoding="utf-8") == repaired_content
    assert "calc.py" in result.state.initial_snapshot
    assert "+    if b == 0" in result.state.changed_files["calc.py"]
    assert "+        raise ValueError" in result.state.changed_files["calc.py"]
    assert result.state.changed_file_hashes["calc.py"] == sha256_file_streaming(calculator)
    assert result.state.last_test_result is not None
    last_test = json.loads(result.state.last_test_result)
    assert last_test["ok"] is True
    assert last_test["meta"]["exit_code"] == 0
    assert {event["event"] for event in events} >= {
        "model_request",
        "tool_call",
        "tool_result",
        "run_completed",
    }
    first_tool_call = next(event for event in events if event["event"] == "tool_call")
    assert first_tool_call["data"]["tool_call_id"] == "read-1"
    assert '"path": "calc.py"' in first_tool_call["data"]["arguments_json"]
    model_response = next(
        event for event in events if event["event"] == "model_response"
    )
    tool_result = next(event for event in events if event["event"] == "tool_result")
    assert model_response["data"]["duration_ms"] >= 0
    assert tool_result["data"]["duration_ms"] >= 0
    assert "请始终使用中文" in str(provider.requests[0][0]["content"])
    assert "user's explicit plan is an execution contract" in str(
        provider.requests[0][0]["content"]
    )


def test_explicit_no_read_constraint_blocks_model_deviation(tmp_path: Path) -> None:
    """Reject a speculative read when the user's plan explicitly forbids reads."""

    workspace, _ = _create_buggy_workspace(tmp_path)
    provider = FakeProvider(
        [
            _tool_turn(
                "read-forbidden",
                "read_file",
                {
                    "path": "calc.py",
                    "start_line": 1,
                    "max_lines": 20,
                    "max_chars": 2_000,
                },
            ),
            _final_turn("已遵循用户计划，不读取已有文件。"),
        ]
    )

    result = run_agent(
        "创建一个全新的说明文件，不要读取任何现有文件。",
        AgentConfig(workspace=workspace, provider=provider),
    )

    assert result.status == "completed"
    observed = provider.requests[1][-1]
    payload = json.loads(observed["content"])
    assert observed["role"] == "tool"
    assert payload["error"]["code"] == "task_scope_violation"


def test_run_agent_retries_transient_model_errors(tmp_path: Path) -> None:
    workspace, _ = _create_buggy_workspace(tmp_path)
    provider = FakeProvider([TimeoutError("first"), ConnectionError("second"), _final_turn()])
    delays: list[float] = []
    cfg = AgentConfig(
        workspace=workspace,
        provider=provider,
        max_api_attempts=3,
        retry_base_seconds=0.1,
        sleep_fn=delays.append,
    )

    result = run_agent("finish without tools", cfg)

    assert result.status == "completed"
    assert provider.calls == 3
    assert delays == [0.1, 0.2]


def test_run_agent_stops_at_max_steps(tmp_path: Path) -> None:
    workspace, _ = _create_buggy_workspace(tmp_path)
    provider = FakeProvider(
        [
            _tool_turn(
                f"read-{index}",
                "read_file",
                {"path": "calc.py", "start_line": index + 1, "max_lines": 1, "max_chars": 1_000},
            )
            for index in range(2)
        ]
    )
    cfg = AgentConfig(workspace=workspace, provider=provider, max_steps=2)

    result = run_agent("keep reading", cfg)

    assert result.status == "stopped"
    assert result.reason == "max_steps"
    assert result.state.step == 2


def test_run_agent_stops_on_repeated_tool_calls(tmp_path: Path) -> None:
    workspace, _ = _create_buggy_workspace(tmp_path)
    arguments = {"path": "calc.py", "start_line": 1, "max_lines": 1, "max_chars": 1_000}
    provider = FakeProvider(
        [_tool_turn(f"read-{index}", "read_file", arguments) for index in range(4)]
    )
    cfg = AgentConfig(workspace=workspace, provider=provider, max_steps=6, max_same_call=3)

    result = run_agent("repeat reads", cfg)

    assert result.status == "stopped"
    assert result.reason == "repeated_call_limit"


def test_interactive_rejection_prevents_write(tmp_path: Path) -> None:
    workspace, calculator = _create_buggy_workspace(tmp_path)
    original_content = calculator.read_text(encoding="utf-8")
    provider = FakeProvider(
        [
            _tool_turn(
                "write-1",
                "write_file",
                {
                    "path": "calc.py",
                    "content": "changed\n",
                    "expected_sha256": sha256_file_streaming(calculator),
                },
            ),
            _final_turn("Write was rejected."),
        ]
    )
    requested_paths: list[str] = []

    def reject(path: str) -> bool:
        requested_paths.append(path)
        return False

    cfg = AgentConfig(
        workspace=workspace,
        provider=provider,
        interactive=True,
        confirm_write=reject,
    )

    result = run_agent("attempt a write", cfg)

    assert result.status == "completed"
    assert requested_paths == ["calc.py"]
    assert calculator.read_text(encoding="utf-8") == original_content
    assert result.state.changed_files == {}


def test_run_agent_honors_cooperative_stop_before_next_tool(tmp_path: Path) -> None:
    """Stop at a safe checkpoint while retaining messages already collected."""

    workspace, calculator = _create_buggy_workspace(tmp_path)
    provider = FakeProvider(
        [
            _tool_turn(
                "write-1",
                "write_file",
                {
                    "path": "calc.py",
                    "content": "should not be written\n",
                    "expected_sha256": sha256_file_streaming(calculator),
                },
            )
        ]
    )
    checks = 0

    def should_stop() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 3

    original = calculator.read_text(encoding="utf-8")
    cfg = AgentConfig(
        workspace=workspace,
        provider=provider,
        should_stop=should_stop,
    )

    result = run_agent("stop before executing the tool", cfg)

    assert result.status == "stopped"
    assert result.reason == "user_stopped"
    assert calculator.read_text(encoding="utf-8") == original
    assert result.state.messages[0]["role"] == "system"
    assert result.state.messages[1]["role"] == "user"


def test_goal_mode_preserves_complete_reasoning_in_agent_state(tmp_path: Path) -> None:
    """Keep provider reasoning available for late GUI delivery and inspection."""

    workspace, _ = _create_buggy_workspace(tmp_path)
    reasoning = "Inspect the current behavior.\nThen validate the proposed fix."
    turn = AssistantTurn(
        content="Done.",
        tool_calls=[],
        protocol_message={
            "role": "assistant",
            "content": "Done.",
            "reasoning_content": reasoning,
        },
        finish_reason="stop",
    )
    cfg = AgentConfig(
        workspace=workspace,
        provider=FakeProvider([turn]),
        mode="goal",
    )

    result = run_agent("reason deeply", cfg)

    assert result.status == "completed"
    assert result.state.reasoning == reasoning


def test_agent_loop_forwards_reasoning_chunks_while_retaining_final_state(
    tmp_path: Path,
) -> None:
    """Keep the core GUI-agnostic while exposing native reasoning deltas."""

    class StreamingProvider(ModelProvider):
        def complete(
            self,
            messages: Sequence[dict[str, Any]],
            tools: Sequence[dict[str, Any]],
        ) -> AssistantTurn:
            _ = (messages, tools)
            raise AssertionError("streaming path expected")

        def complete_stream(
            self,
            messages: Sequence[dict[str, Any]],
            tools: Sequence[dict[str, Any]],
            on_content_chunk: Callable[[str], None],
            on_reasoning_chunk: Callable[[str], None] | None = None,
        ) -> AssistantTurn:
            _ = (messages, tools)
            assert on_reasoning_chunk is not None
            on_reasoning_chunk("先规划")
            on_reasoning_chunk("，再验证")
            on_content_chunk("完成。")
            return AssistantTurn(
                content="完成。",
                tool_calls=[],
                protocol_message={
                    "role": "assistant",
                    "content": "完成。",
                    "reasoning_content": "先规划，再验证",
                },
                finish_reason="stop",
            )

    workspace, _ = _create_buggy_workspace(tmp_path)
    reasoning_deltas: list[str] = []
    content_deltas: list[str] = []
    cfg = AgentConfig(
        workspace=workspace,
        provider=StreamingProvider(),
        mode="goal",
        on_token=content_deltas.append,
        on_reasoning_token=reasoning_deltas.append,
    )

    result = run_agent("深入分析", cfg)

    assert reasoning_deltas == ["先规划", "，再验证"]
    assert content_deltas == ["完成。"]
    assert result.state.reasoning == "先规划，再验证"
