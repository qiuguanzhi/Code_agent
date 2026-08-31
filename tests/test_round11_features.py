"""Deterministic coverage for the round-11 capability extensions."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QApplication

from agent.context import ContextUsage, fit_context
from agent.loop import AgentConfig, run_agent
from agent.state import AgentState, AssistantTurn, ToolCall
from gui.theme import DARK_COLORS, LIGHT_COLORS
from gui.widgets import ContextRing
from providers.base import ModelProvider
from skills.base import AgentContext, Skill, SkillResult
from skills.registry import SkillRegistry
from tools.registry import ToolRegistry


class ListFilesSkill(Skill):
    """Test-only multi-tool capability using the portable listing command."""

    name = "list_files"
    description = "列出当前工作区中的普通目录项。"
    parameters_schema = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    required_permissions = frozenset({"process"})

    def execute(self, params: dict[str, Any], context: AgentContext) -> SkillResult:
        """Delegate to the validated local run_command tool."""

        _ = params
        result = context.call_tool(
            "run_command",
            {"argv": ["ls"], "cwd": ".", "timeout_seconds": 5, "max_output_chars": 5_000},
        )
        return SkillResult(
            bool(result.get("ok")),
            data=result.get("data"),
            error=result.get("error"),
        )


class HighRiskSkill(Skill):
    """Test-only capability that must never execute without confirmation."""

    name = "high_risk_test"
    description = "验证高风险技能确认。"
    high_risk = True

    def execute(self, params: dict[str, Any], context: AgentContext) -> SkillResult:
        """Return success after the registry has approved the call."""

        _ = (params, context)
        return SkillResult(True, "approved")


class ShadowNativeSkill(HighRiskSkill):
    """Invalid test skill whose name collides with a native tool."""

    name = "read_file"


class SequenceProvider(ModelProvider):
    """Network-free provider returning a fixed sequence of turns."""

    def __init__(self, turns: Sequence[AssistantTurn]) -> None:
        self.turns = list(turns)

    def complete(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
    ) -> AssistantTurn:
        """Return the next scripted response."""

        _ = (messages, tools)
        return self.turns.pop(0)


def _tool_turn(call_id: str, name: str) -> AssistantTurn:
    """Build one empty-argument native tool call."""

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


def _final_turn(text: str = "完成") -> AssistantTurn:
    """Build one terminal text turn."""

    return AssistantTurn(text, [], {"role": "assistant", "content": text})


def test_skill_registry_routes_nested_native_tool(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "alpha.py").write_text("pass\n", encoding="utf-8")
    skills = SkillRegistry(granted_permissions={"process"})
    skills.register(ListFilesSkill)
    registry = ToolRegistry(workspace, skill_registry=skills)
    state = AgentState()

    encoded = registry.execute_one_call(ToolCall("skill-1", "list_files", "{}"), state)
    result = json.loads(encoded)

    assert result["ok"] is True
    assert "alpha.py" in result["data"]
    assert result["meta"]["kind"] == "skill"
    assert any(
        schema["function"]["name"] == "list_files" for schema in registry.schemas
    )


def test_disabled_skill_is_not_exposed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skills = SkillRegistry(granted_permissions={"process"}, enabled_names=set())
    skills.register(ListFilesSkill)
    registry = ToolRegistry(workspace, skill_registry=skills)

    result = json.loads(
        registry.execute_one_call(ToolCall("skill-1", "list_files", "{}"), AgentState())
    )

    assert result["error"]["code"] == "unknown_tool"
    assert all(schema["function"]["name"] != "list_files" for schema in registry.schemas)


def test_high_risk_skill_requires_explicit_confirmation(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    denied = SkillRegistry()
    denied.register(HighRiskSkill)
    denied_registry = ToolRegistry(workspace, skill_registry=denied)
    denied_result = json.loads(
        denied_registry.execute_one_call(
            ToolCall("risk-denied", "high_risk_test", "{}"), AgentState()
        )
    )
    approved = SkillRegistry(confirm_high_risk=lambda skill: skill.name == "high_risk_test")
    approved.register(HighRiskSkill)
    approved_registry = ToolRegistry(workspace, skill_registry=approved)
    approved_result = json.loads(
        approved_registry.execute_one_call(
            ToolCall("risk-approved", "high_risk_test", "{}"), AgentState()
        )
    )

    assert denied_result["error"]["code"] == "skill_confirmation_required"
    assert approved_result["ok"] is True


def test_skill_cannot_shadow_native_tool(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skills = SkillRegistry()
    skills.register(ShadowNativeSkill)

    with pytest.raises(ValueError, match="cannot shadow native tools"):
        ToolRegistry(workspace, skill_registry=skills)


def test_agent_can_call_registered_skill(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "beta.txt").write_text("ok", encoding="utf-8")
    skills = SkillRegistry(granted_permissions={"process"})
    skills.register(ListFilesSkill)
    provider = SequenceProvider([_tool_turn("skill-1", "list_files"), _final_turn()])

    result = run_agent(
        "列出文件",
        AgentConfig(workspace=workspace, provider=provider, skill_registry=skills),
    )

    assert result.status == "completed"
    tool_message = next(message for message in result.state.messages if message["role"] == "tool")
    assert "beta.txt" in tool_message["content"]


def test_fit_context_reports_proactive_compression() -> None:
    messages = [{"role": "system", "content": "rules"}]
    for index in range(8):
        messages.extend(
            [
                {"role": "user", "content": f"request-{index}-" + "x" * 200},
                {"role": "assistant", "content": f"answer-{index}-" + "y" * 200},
            ]
        )
    usages: list[ContextUsage] = []

    fitted = fit_context(messages, [], 4_000, usage_callback=usages.append)

    assert usages and usages[0].compressed is True
    assert usages[0].used_tokens <= 3_200
    assert usages[0].released_tokens > 0
    assert any(str(message.get("content", "")).startswith("WORK_MEMORY_JSON") for message in fitted)


def test_duration_limit_stops_before_model_call(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    provider = SequenceProvider([_final_turn()])
    times = iter([0.0, 2.0])

    result = run_agent(
        "等待",
        AgentConfig(
            workspace=workspace,
            provider=provider,
            max_duration_seconds=1.0,
            time_fn=lambda: next(times),
        ),
    )

    assert result.reason == "max_duration"
    assert result.answer == "⏱️ 执行超时（超过 1 秒）"
    assert len(provider.turns) == 1


def test_max_steps_can_be_extended_once(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    provider = SequenceProvider([_tool_turn("one", "list_files"), _final_turn()])
    skills = SkillRegistry(granted_permissions={"process"})
    skills.register(ListFilesSkill)
    decisions: list[tuple[int, int, int]] = []

    result = run_agent(
        "列出后总结",
        AgentConfig(
            workspace=workspace,
            provider=provider,
            max_steps=1,
            skill_registry=skills,
            confirm_step_extension=lambda step, limit, count: (
                decisions.append((step, limit, count)) or True
            ),
        ),
    )

    assert result.status == "completed"
    assert result.state.step == 2
    assert result.state.override_max_steps is True
    assert result.state.step_extensions == 1
    assert decisions == [(1, 1, 0)]


@pytest.fixture
def qt_app() -> QApplication:
    """Return one process-wide offscreen Qt application."""

    existing = QApplication.instance()
    return existing if existing is not None else QApplication([])


def test_context_ring_thresholds_and_theme(qt_app: QApplication) -> None:
    _ = qt_app
    ring = ContextRing(DARK_COLORS)
    ring.set_usage(59, 100)
    assert ring.percentage == 59
    assert ring.level_color().name() == DARK_COLORS["success"].lower()
    ring.set_usage(70, 100)
    assert ring.level_color().name() == DARK_COLORS["warning"].lower()
    ring.set_usage(90, 100)
    assert ring.level_color().name() == DARK_COLORS["error"].lower()
    assert "90 / 100" in ring.toolTip()
    ring.set_colors(LIGHT_COLORS)
    assert ring.level_color().name() == LIGHT_COLORS["error"].lower()
    ring.show()
    qt_app.processEvents()
    assert not ring.grab().isNull()
    ring.deleteLater()
