"""Regression coverage for manual Skill management and topmost splash behavior."""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from agent.state import AgentState, ToolCall
from gui.main_window import MainWindow
from gui.skill_dialog import SkillManagerDialog
from gui.splash_screen import SplashScreen
from gui.widgets import AddSkillDialog
from skills.base import AgentContext
from skills.registry import SkillCodeSafetyError, SkillRegistry
from tools.registry import ToolRegistry


HELLO_SKILL_SOURCE = '''class HelloSkill(Skill):
    parameters_schema = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    def execute(self, params: dict[str, Any], context: AgentContext) -> SkillResult:
        _ = (params, context)
        return SkillResult(True, "Hello")
'''


@pytest.fixture
def qt_app() -> Generator[QApplication, None, None]:
    """Return the process-wide offscreen Qt application."""

    existing = QApplication.instance()
    app = existing if existing is not None else QApplication([])
    yield app
    app.processEvents()


def test_registry_registers_persists_executes_and_deletes_manual_skill(
    tmp_path: Path,
) -> None:
    user_dir = tmp_path / "managed-user-skills"
    registry = SkillRegistry(enabled_names=set(), user_skill_dir=user_dir)

    skill = registry.register_from_code(
        name="hello_skill",
        description="Return a deterministic greeting.",
        source_code=HELLO_SKILL_SOURCE,
    )

    assert skill.name == "hello_skill"
    assert skill.built_in is False
    assert skill.source_path == (user_dir / "skill_hello_skill.py").resolve()
    assert skill.source_path.is_file()
    assert skill.source_path.read_text(encoding="utf-8").startswith(
        "# CEREBRO_SKILL_META: "
    )
    assert registry.is_enabled("hello_skill") is True
    tool_registry = ToolRegistry(tmp_path, skill_registry=registry)
    result = json.loads(
        tool_registry.execute_one_call(
            ToolCall("dynamic-skill-1", "hello_skill", "{}"),
            AgentState(),
        )
    )
    assert result["ok"] is True
    assert result["data"] == "Hello"

    rediscovered = SkillRegistry.discover_all(
        enabled_names={"hello_skill"},
        user_skill_dir=user_dir,
    )
    assert rediscovered.get_skill("hello_skill") is not None
    assert rediscovered.is_enabled("hello_skill") is True

    removed = rediscovered.unregister("hello_skill")
    assert removed.name == "hello_skill"
    assert not (user_dir / "skill_hello_skill.py").exists()
    denied = rediscovered.execute(
        "hello_skill",
        {},
        AgentContext(workspace=tmp_path, execute_tool=lambda name, params: {}),
    )
    assert denied["error"]["code"] == "unknown_skill"


def test_registry_restricts_manual_code_and_protects_builtin_skill(
    tmp_path: Path,
) -> None:
    registry = SkillRegistry.discover_all(user_skill_dir=tmp_path / "user")

    with pytest.raises(ValueError, match="语法错误"):
        registry.register_from_code(
            name="broken_skill",
            description="Broken.",
            source_code="class BrokenSkill(Skill)\n    pass",
        )
    with pytest.raises(ValueError, match="禁止 import"):
        registry.register_from_code(
            name="unsafe_import",
            description="Unsafe.",
            source_code="import os\n" + HELLO_SKILL_SOURCE,
        )
    dangerous_source = HELLO_SKILL_SOURCE.replace(
        "return SkillResult(True, \"Hello\")",
        "return SkillResult(True, eval(\"1 + 1\"))",
    )
    with pytest.raises(SkillCodeSafetyError, match="危险函数 eval"):
        registry.register_from_code(
            name="unsafe_eval",
            description="Unsafe.",
            source_code=dangerous_source,
        )
    with pytest.raises(ValueError, match="不可覆盖原生工具"):
        registry.register_from_code(
            name="read_file",
            description="Shadow native tool.",
            source_code=HELLO_SKILL_SOURCE,
        )

    builtin = registry.get_skill("code_reviewer")
    assert builtin is not None and builtin.built_in is True
    with pytest.raises(ValueError, match="内置 Skill 不可删除"):
        registry.unregister("code_reviewer")


def test_skill_manager_ui_adds_and_deletes_but_locks_builtin(
    qt_app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = SkillRegistry.discover_all(user_skill_dir=tmp_path / "user")
    dialog = SkillManagerDialog(registry)
    added_spy = QSignalSpy(dialog.skill_added)
    deleted_spy = QSignalSpy(dialog.skill_deleted)
    monkeypatch.setattr(
        AddSkillDialog,
        "exec",
        lambda self: QDialog.DialogCode.Accepted,
    )
    monkeypatch.setattr(
        AddSkillDialog,
        "definition",
        lambda self: {
            "name": "hello_skill",
            "description": "Return a deterministic greeting.",
            "source_code": HELLO_SKILL_SOURCE,
            "required_permissions": frozenset(),
        },
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    try:
        dialog.show()
        dialog.add_skill()
        qt_app.processEvents()

        assert added_spy.count() == 1
        assert added_spy.at(0)[0] == "hello_skill"
        assert dialog.registry.get_skill("hello_skill") is not None
        assert dialog.delete_button.isEnabled() is True
        assert "用户 Skill" in dialog.origin_value.text()

        dialog.delete_selected_skill()
        qt_app.processEvents()
        assert deleted_spy.count() == 1
        assert deleted_spy.at(0)[0] == "hello_skill"
        assert dialog.registry.get_skill("hello_skill") is None

        dialog._select_name("code_reviewer")
        assert dialog.delete_button.isEnabled() is False
        assert "系统 Skill" in dialog.origin_value.text()
        assert dialog.windowFlags() & Qt.WindowType.FramelessWindowHint
        assert dialog.windowOpacity() == pytest.approx(0.97, abs=0.01)
    finally:
        dialog.close()
        qt_app.processEvents()


def test_add_skill_dialog_exposes_form_and_permissions(
    qt_app: QApplication,
) -> None:
    dialog = AddSkillDialog()
    try:
        dialog.name_input.setText("weather_query")
        dialog.description_input.setPlainText("查询天气。")
        dialog.permission_checks["network"].setChecked(True)
        dialog.permission_checks["process"].setChecked(True)
        definition = dialog.definition()
        assert definition["name"] == "weather_query"
        assert definition["description"] == "查询天气。"
        assert definition["required_permissions"] == frozenset(
            {"network", "process"}
        )
        assert "class CustomSkill(Skill)" in str(definition["source_code"])
    finally:
        dialog.close()
        qt_app.processEvents()


def test_splash_is_topmost_centered_and_click_skips(
    qt_app: QApplication,
) -> None:
    splash = SplashScreen()
    finished_spy = QSignalSpy(splash.finished)
    try:
        flags = splash.windowFlags()
        assert flags & Qt.WindowType.WindowStaysOnTopHint
        assert flags & Qt.WindowType.FramelessWindowHint
        assert flags & Qt.WindowType.Tool
        assert splash.size().width() == 800
        assert splash.size().height() == 500

        splash.start()
        qt_app.processEvents()
        screen = QApplication.primaryScreen()
        if screen is not None:
            assert splash.frameGeometry().center() == screen.availableGeometry().center()
        QTest.mouseClick(splash, Qt.MouseButton.LeftButton, pos=QPoint(20, 20))
        qt_app.processEvents()
        assert finished_spy.count() == 1
    finally:
        if not finished_spy.count():
            splash.skip()
        qt_app.processEvents()


def test_main_window_does_not_inherit_topmost_flag(
    qt_app: QApplication,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(workspace, settings=settings)
    try:
        assert not (window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
    finally:
        window.close()
        qt_app.processEvents()
