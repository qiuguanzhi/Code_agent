"""Regression coverage for round-12 context and continuation controls."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from agent.context import MAX_INPUT_TOKENS, ContextUsage, fit_context
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from agent.loop import AgentConfig, run_agent
from agent.state import AssistantTurn, ToolCall
from gui.main_window import MainWindow
from gui.worker import AgentWorker
from main import build_parser
from providers.base import ModelProvider
from providers.openai_compatible import MAX_OUTPUT_TOKENS, OpenAICompatibleProvider
from tools.schemas import get_tool_schemas


class CapturingCompletions:
    """Capture an SDK-shaped request and return a final text response."""

    def __init__(self) -> None:
        self.last_request: dict[str, Any] | None = None

    def create(self, **kwargs: Any) -> Any:
        """Record request values without any network access."""

        self.last_request = kwargs
        message = SimpleNamespace(content="完成", tool_calls=None, reasoning_content=None)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")],
            usage=None,
        )


class NeverCalledProvider(ModelProvider):
    """Config-only provider whose transport must never be used."""

    def complete(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
    ) -> AssistantTurn:
        """Fail if a config-default test accidentally invokes transport."""

        _ = (messages, tools)
        raise AssertionError("provider should not be called")


class SequenceProvider(ModelProvider):
    """Return a long deterministic sequence without network access."""

    def __init__(self, turns: Sequence[AssistantTurn]) -> None:
        self.turns = list(turns)

    def complete(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
    ) -> AssistantTurn:
        """Return the next prebuilt response."""

        _ = (messages, tools)
        return self.turns.pop(0)


def _read_turn(index: int) -> AssistantTurn:
    """Build a unique read call so repeat detection remains meaningful."""

    arguments = json.dumps(
        {
            "path": "large.txt",
            "start_line": index,
            "max_lines": 1,
            "max_chars": 100,
        }
    )
    call_id = f"read-{index}"
    call = ToolCall(call_id, "read_file", arguments)
    return AssistantTurn(
        None,
        [call],
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": "read_file", "arguments": arguments},
                }
            ],
        },
    )


def _final_turn() -> AssistantTurn:
    """Build a successful terminal answer."""

    return AssistantTurn("完成", [], {"role": "assistant", "content": "完成"})


def _unknown_tool_turn(index: int) -> AssistantTurn:
    """Build a cheap unique call used only to advance deterministic steps."""

    name = f"unregistered_{index}"
    call_id = f"unknown-{index}"
    call = ToolCall(call_id, name, "{}")
    return AssistantTurn(
        None,
        [call],
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": "{}"},
                }
            ],
        },
    )


def test_large_message_fits_new_320k_budget_without_compression() -> None:
    messages = [
        {"role": "system", "content": "遵循用户任务。"},
        {"role": "user", "content": "中" * 150_001},
    ]
    usages: list[ContextUsage] = []

    fitted = fit_context(
        messages,
        get_tool_schemas(),
        MAX_INPUT_TOKENS,
        usage_callback=usages.append,
    )

    assert MAX_INPUT_TOKENS == 320_000
    assert fitted == messages
    assert usages[0].compressed is False
    assert usages[0].budget_tokens == 320_000
    assert usages[0].used_tokens < int(320_000 * 0.80)


def test_provider_requests_16384_output_tokens() -> None:
    completions = CapturingCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    provider = OpenAICompatibleProvider(client, "fake-model")

    provider.complete([{"role": "user", "content": "任务"}], get_tool_schemas())

    assert MAX_OUTPUT_TOKENS == 16_384
    assert provider.max_tokens == 16_384
    assert completions.last_request is not None
    assert completions.last_request["max_tokens"] == 16_384


def test_agent_config_uses_expanded_context_default(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = AgentConfig(workspace=workspace, provider=NeverCalledProvider())

    assert config.input_token_budget == 320_000


def test_default_step_limit_is_200_in_core_and_cli(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = AgentConfig(workspace=workspace, provider=NeverCalledProvider())

    assert config.max_steps == 200
    assert build_parser().parse_args([]).max_steps == 200


def test_step_limit_can_be_extended_more_than_three_times(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "large.txt").write_text(
        "".join(f"line-{index}\n" for index in range(1, 100)),
        encoding="utf-8",
    )
    provider = SequenceProvider([*(_read_turn(index) for index in range(1, 152)), _final_turn()])
    decisions: list[tuple[int, int, int]] = []
    events: list[dict[str, Any]] = []

    result = run_agent(
        "持续读取后总结",
        AgentConfig(
            workspace=workspace,
            provider=provider,
            max_steps=1,
            confirm_step_extension=lambda step, limit, count: (
                decisions.append((step, limit, count)) or True
            ),
        ),
        events.append,
    )

    assert result.status == "completed"
    assert result.state.step == 152
    assert result.state.step_extensions == 4
    assert [limit for _, limit, _ in decisions] == [1, 51, 101, 151]
    approvals = [event for event in events if event["event"] == "step_extension_approved"]
    assert [event["data"]["max_steps"] for event in approvals] == [51, 101, 151, 201]
    assert all("+50" in event["message"] for event in approvals)


def test_declining_extension_stops_without_another_prompt(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "large.txt").write_text("one\n", encoding="utf-8")
    provider = SequenceProvider([_read_turn(1), _final_turn()])
    decisions = 0

    def decline(step: int, limit: int, count: int) -> bool:
        """Reject the first prompt and count invocations."""

        nonlocal decisions
        _ = (step, limit, count)
        decisions += 1
        return False

    result = run_agent(
        "读取",
        AgentConfig(
            workspace=workspace,
            provider=provider,
            max_steps=1,
            confirm_step_extension=decline,
        ),
    )

    assert result.reason == "user_declined_step_extension"
    assert decisions == 1
    assert len(provider.turns) == 1


def test_150_step_task_does_not_prompt_before_200(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    provider = SequenceProvider(
        [*(_unknown_tool_turn(index) for index in range(1, 150)), _final_turn()]
    )

    def unexpected_prompt(step: int, limit: int, count: int) -> bool:
        """Fail if the new 200-step default asks too early."""

        _ = (step, limit, count)
        raise AssertionError("continuation prompt appeared before step 200")

    result = run_agent(
        "执行 150 步任务",
        AgentConfig(
            workspace=workspace,
            provider=provider,
            confirm_step_extension=unexpected_prompt,
        ),
    )

    assert result.status == "completed"
    assert result.state.step == 150
    assert result.state.step_extensions == 0


def test_200_and_250_limits_each_allow_another_50_steps(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    provider = SequenceProvider(
        [*(_unknown_tool_turn(index) for index in range(1, 251)), _final_turn()]
    )
    decisions: list[tuple[int, int, int]] = []
    events: list[dict[str, Any]] = []

    result = run_agent(
        "跨越两个默认阈值",
        AgentConfig(
            workspace=workspace,
            provider=provider,
            confirm_step_extension=lambda step, limit, count: (
                decisions.append((step, limit, count)) or True
            ),
        ),
        events.append,
    )

    assert result.status == "completed"
    assert result.state.step == 251
    assert decisions == [(200, 200, 0), (250, 250, 1)]
    approvals = [event for event in events if event["event"] == "step_extension_approved"]
    assert [event["data"]["max_steps"] for event in approvals] == [250, 300]
    assert all("步数上限 +50" in event["message"] for event in approvals)


@pytest.fixture
def qt_app() -> QApplication:
    """Return the process-wide offscreen QApplication."""

    existing = QApplication.instance()
    return existing if existing is not None else QApplication([])


def test_gui_displays_30_step_default_and_context_budget_log(
    qt_app: QApplication,
    tmp_path: Path,
) -> None:
    settings = QSettings(
        str(tmp_path / "round12.ini"),
        QSettings.Format.IniFormat,
    )
    window = MainWindow(settings=settings)
    worker = AgentWorker(provider=NeverCalledProvider())
    logs: list[tuple[int, str, str, str, str]] = []
    contexts: list[tuple[int, int]] = []
    worker.log_signal.connect(lambda *values: logs.append(values))
    worker.context_signal.connect(lambda used, budget: contexts.append((used, budget)))

    worker._handle_update(
        {
            "event": "context_budget_configured",
            "step": 0,
            "message": "budget",
            "data": {"used_tokens": 0, "budget_tokens": 320_000},
        }
    )
    worker._handle_update(
        {
            "event": "step_started",
            "step": 127,
            "message": "step",
            "data": {"current_step": 127, "max_steps": 200},
        }
    )
    worker.step_progress_signal.connect(window._update_step_progress)
    worker.step_progress_signal.emit(127, 200)
    qt_app.processEvents()

    assert window.max_steps == 200
    assert window.step_counter_label.text() == "步数：127/200"
    assert contexts == [(0, 320_000)]
    assert any("上下文预算已扩容至 320K Tokens" in entry[-1] for entry in logs)
    worker.deleteLater()
    window.close()
    qt_app.processEvents()
