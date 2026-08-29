"""Headless regression tests for the PySide6 desktop interface."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections.abc import Callable, Generator, Sequence
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QMimeData, QSettings, Qt, QUrl, qInstallMessageHandler
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMessageBox,
)

import main as main_module
from agent.state import AssistantTurn, ToolCall
from gui.main_window import MainWindow
from gui.session import ConversationStore
from gui.theme import DARK_COLORS, DARK_THEME, LIGHT_COLORS, LIGHT_THEME
from gui.worker import AgentWorker
from providers.base import ModelProvider
from tools.filesystem import sha256_file_streaming
from utils.snapshot import SNAPSHOT_META_KEY


class FakeProvider(ModelProvider):
    """Return deterministic turns without API keys or network access."""

    def __init__(self, turns: Sequence[AssistantTurn]) -> None:
        self.turns = list(turns)

    def complete(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
    ) -> AssistantTurn:
        """Return the next predefined turn."""

        _ = (messages, tools)
        return self.turns.pop(0)


class FakeDropEvent:
    """Small duck-typed drop event for testing the window handler."""

    def __init__(self, path: Path) -> None:
        self.mime = QMimeData()
        self.mime.setUrls([QUrl.fromLocalFile(str(path))])
        self.accepted = False

    def mimeData(self) -> QMimeData:
        """Return local-file MIME data."""

        return self.mime

    def acceptProposedAction(self) -> None:
        """Record acceptance."""

        self.accepted = True

    def ignore(self) -> None:
        """Record rejection."""

        self.accepted = False


def _tool_turn(call_id: str, name: str, arguments: dict[str, Any]) -> AssistantTurn:
    """Build one normalized native tool-call turn."""

    arguments_json = json.dumps(arguments)
    return AssistantTurn(
        content=None,
        tool_calls=[ToolCall(call_id, name, arguments_json)],
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


def _final_turn(content: str = "任务已完成。") -> AssistantTurn:
    """Build one normalized terminal assistant turn."""

    return AssistantTurn(
        content=content,
        tool_calls=[],
        protocol_message={"role": "assistant", "content": content},
        finish_reason="stop",
    )


def _read_provider() -> FakeProvider:
    """Build a read-then-finish provider for GUI integration tests."""

    return FakeProvider(
        [
            _tool_turn(
                "read-1",
                "read_file",
                {
                    "path": "calc.py",
                    "start_line": 1,
                    "max_lines": 100,
                    "max_chars": 10_000,
                },
            ),
            _final_turn(),
        ]
    )


def _two_tool_provider() -> FakeProvider:
    """Build two sequential tools to verify log cardinality."""

    return FakeProvider(
        [
            _tool_turn(
                "read-1",
                "read_file",
                {
                    "path": "calc.py",
                    "start_line": 1,
                    "max_lines": 100,
                    "max_chars": 10_000,
                },
            ),
            _tool_turn(
                "run-1",
                "run_command",
                {
                    "argv": [sys.executable, "-c", "print('ok')"],
                    "cwd": ".",
                    "timeout_seconds": 5,
                    "max_output_chars": 1_000,
                },
            ),
            _final_turn(),
        ]
    )


def _write_provider(target: Path, new_content: str) -> FakeProvider:
    """Build one write attempt followed by a final answer."""

    return FakeProvider(
        [
            _tool_turn(
                "write-1",
                "write_file",
                {
                    "path": "calc.py",
                    "content": new_content,
                    "expected_sha256": sha256_file_streaming(target),
                },
            ),
            _final_turn("修改流程结束。"),
        ]
    )


def _new_file_write_provider(path: str, content: str) -> FakeProvider:
    """Build a new-file write attempt that must still be approved."""

    return FakeProvider(
        [
            _tool_turn(
                "write-new-1",
                "write_file",
                {
                    "path": path,
                    "content": content,
                    "expected_sha256": "",
                },
            ),
            _final_turn("新文件流程结束。"),
        ]
    )


def _wait_until(
    app: QApplication,
    predicate: Callable[[], bool],
    timeout: float = 2.0,
) -> bool:
    """Pump the Qt loop until a condition becomes true or times out."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    app.processEvents()
    return predicate()


@pytest.fixture(scope="module")
def qt_app() -> Generator[QApplication, None, None]:
    """Provide one offscreen Qt application for widget and signal tests."""

    existing = QApplication.instance()
    app = existing if existing is not None else QApplication([])
    yield app
    app.processEvents()


@pytest.fixture
def gui_settings(tmp_path: Path) -> QSettings:
    """Isolate persistent GUI state for every test."""

    settings = QSettings(str(tmp_path / "gui.ini"), QSettings.Format.IniFormat)
    settings.clear()
    settings.sync()
    return settings


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create a minimal readable and writable Agent workspace."""

    root = tmp_path / "workspace"
    root.mkdir()
    (root / "calc.py").write_text(
        "def divide(a: float, b: float) -> float:\n    return a / b\n",
        encoding="utf-8",
        newline="\n",
    )
    return root


def test_themes_cover_dark_and_light_palettes() -> None:
    """Provide complete ChatGPT-inspired global QSS in both themes."""

    for color in (
        "#343541",
        "#202123",
        "#2d2d3a",
        "#ececf1",
        "#40414f",
    ):
        assert color in DARK_THEME
    for color in ("#f7f7f8", "#ffffff", "#202123", "#10a37f", "#ececf1"):
        assert color in LIGHT_THEME
    for stylesheet in (DARK_THEME, LIGHT_THEME):
        assert 'font-family: "Segoe UI"' in stylesheet
        assert "border-radius: 12px" in stylesheet or "border-radius: 10px" in stylesheet
        assert "width: 6px" in stylesheet
    assert DARK_COLORS["user_bubble"] == "#244c43"
    assert LIGHT_COLORS["user_bubble"] == "#e9f6f1"
    assert "QFrame#userBubble" in DARK_THEME
    assert "QFrame#assistantBubble" in DARK_THEME
    assert "border-radius: 12px" in DARK_THEME
    assert "QLabel#emptyTabPlaceholder" not in DARK_THEME


def test_main_window_has_three_resizable_columns_and_empty_input(
    qt_app: QApplication,
    workspace: Path,
    gui_settings: QSettings,
) -> None:
    """Build workspace, conversation, and tab panels with the required bounds."""

    window = MainWindow(workspace, settings=gui_settings)
    try:
        assert window.findChild(object, "openWorkspaceAction") is not None
        assert window.findChild(object, "mainToolbar") is not None
        assert window.splitter.orientation() == Qt.Orientation.Horizontal
        assert window.splitter.count() == 3
        assert window.workspace_panel.minimumWidth() == 120
        assert window.workspace_panel.maximumWidth() == 400
        assert window.log_view.isReadOnly() is True
        assert window.code_view.isReadOnly() is True
        assert window.task_input.text() == ""
        assert window.task_input.placeholderText() == ""
        assert window.findChild(object, "runButton") is None
        assert not hasattr(window, "run_action")
        assert window.findChild(object, "selectWorkspaceButton") is not None
        assert window.workspace_tree.model() is window.workspace_model
        assert window.apply_button.isEnabled() is False
        assert window.reject_button.isEnabled() is False
        assert window.decision_widget.isHidden() is True
        assert window.code_tabs.count() == 0
        assert window.code_stack.currentWidget() is window.code_empty_page
        assert window.code_empty_page.objectName() == "codeEmptyPage"
        assert window._empty_code_view.isHidden() is True
        assert window.code_stack.indexOf(window.code_tabs) == -1
        assert window.interactive_confirmation is True
        assert window.interactive_action.isChecked() is True
        assert window.interactive_action.isEnabled() is False
        assert window._fixed_width_font().pointSizeF() > 0
        assert str(workspace) in window.workspace_label.text()
        assert "就绪" in window.status_indicator.text()
    finally:
        window.close()
        qt_app.processEvents()


def test_window_creation_never_uses_an_invalid_font_size(
    qt_app: QApplication,
    workspace: Path,
    gui_settings: QSettings,
) -> None:
    """Prevent the QFont point-size warning observed on Windows startup."""

    qt_messages: list[str] = []
    previous_handler = qInstallMessageHandler(
        lambda message_type, context, message: qt_messages.append(message)
    )
    try:
        window = MainWindow(workspace, settings=gui_settings)
        window.show()
        qt_app.processEvents()
        assert (
            window.log_view.font().pointSizeF() > 0
            or window.log_view.font().pixelSize() > 0
        )
        window.update_code("calc.py", (workspace / "calc.py").read_text(encoding="utf-8"))
        assert (
            window.code_view.font().pointSizeF() > 0
            or window.code_view.font().pixelSize() > 0
        )
        window.close()
        qt_app.processEvents()
    finally:
        qInstallMessageHandler(previous_handler)
    assert not any("Point size <= 0" in message for message in qt_messages)


def test_workspace_switch_updates_tree_and_process_cwd(
    qt_app: QApplication,
    tmp_path: Path,
    gui_settings: QSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Switch the global workspace, tree root, and process working directory."""

    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (second / "new.py").write_text("value = 2\n", encoding="utf-8")
    window = MainWindow(first, settings=gui_settings)
    monkeypatch.setattr(
        QFileDialog,
        "getExistingDirectory",
        lambda *args, **kwargs: str(second),
    )
    try:
        window.select_workspace_button.click()
        qt_app.processEvents()
        assert window.workspace_root == second.resolve()
        assert Path(os.getcwd()).resolve() == second.resolve()
        assert Path(window.workspace_model.rootPath()).resolve() == second.resolve()
        assert "new.py" in window.workspace_files
    finally:
        window.close()
        qt_app.processEvents()


def test_workspace_tree_creates_deletes_and_toggles_directories(
    qt_app: QApplication,
    workspace: Path,
    gui_settings: QSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise safe filesystem management and native tree expansion."""

    window = MainWindow(workspace, settings=gui_settings)
    try:
        window.show()
        folder = window._create_workspace_entry(workspace, "nested", True)
        child = window._create_workspace_entry(folder, "note.txt", False)
        child.write_text("hello\n", encoding="utf-8")
        window._populate_workspace_files()
        assert folder.is_dir()
        assert child.is_file()
        assert "nested/note.txt" in window.workspace_files

        assert _wait_until(
            qt_app,
            lambda: window.workspace_model.index(str(folder)).isValid(),
        )
        folder_index = window.workspace_model.index(str(folder))
        assert window.workspace_tree.isExpanded(folder_index) is False
        window._activate_workspace_index(folder_index)
        assert window.workspace_tree.isExpanded(folder_index) is True
        window._activate_workspace_index(folder_index)
        assert window.workspace_tree.isExpanded(folder_index) is False

        window.workspace_tree.setCurrentIndex(folder_index)
        monkeypatch.setattr(
            QInputDialog,
            "getText",
            lambda *args, **kwargs: ("prompt-created.txt", True),
        )
        window._prompt_create_workspace_entry(is_directory=False)
        prompted_file = folder / "prompt-created.txt"
        assert prompted_file.is_file()
        assert _wait_until(
            qt_app,
            lambda: window.workspace_model.index(str(prompted_file)).isValid(),
        )
        window.workspace_tree.setCurrentIndex(
            window.workspace_model.index(str(prompted_file))
        )
        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
        )
        window._confirm_delete_workspace_entry()
        assert prompted_file.exists() is False

        deleted = window._delete_workspace_entry(folder)
        window._populate_workspace_files()
        assert deleted == folder
        assert folder.exists() is False
        assert "nested/note.txt" not in window.workspace_files
        with pytest.raises(ValueError, match="根目录"):
            window._delete_workspace_entry(workspace)
    finally:
        window.close()
        qt_app.processEvents()


def test_worker_emits_exactly_one_compact_log_per_tool(
    qt_app: QApplication,
    workspace: Path,
) -> None:
    """Suppress model lifecycle chatter while retaining one read tool line."""

    worker = AgentWorker(provider=_read_provider())
    log_spy = QSignalSpy(worker.log_signal)
    code_spy = QSignalSpy(worker.code_signal)
    finished_spy = QSignalSpy(worker.finished_signal)

    worker.start_agent("读取 calc.py", workspace, max_steps=4, interactive=False)
    assert worker.wait(2_000) is True
    qt_app.processEvents()

    assert log_spy.count() == 1
    assert list(log_spy.at(0)) == [1, "🔧", "read_file", "tool_success", ""]
    assert code_spy.count() == 0
    assert finished_spy.count() == 1
    assert finished_spy.at(0)[0] is True


def test_two_tools_produce_exactly_two_log_rows(
    qt_app: QApplication,
    workspace: Path,
) -> None:
    """Keep the visible log count equal to completed tool-call count."""

    worker = AgentWorker(provider=_two_tool_provider())
    log_spy = QSignalSpy(worker.log_signal)
    status_spy = QSignalSpy(worker.tool_status_signal)
    worker.start_agent("读取后运行", workspace, max_steps=5, interactive=False)
    assert worker.wait(4_000) is True
    qt_app.processEvents()

    assert log_spy.count() == 2
    assert [log_spy.at(index)[2] for index in range(2)] == [
        "read_file",
        "run_command",
    ]
    assert status_spy.count() == 4
    assert list(status_spy.at(status_spy.count() - 1)) == [
        "success",
        "run_command",
        "",
    ]


def test_deep_mode_emits_safe_process_summaries_and_quick_mode_does_not(
    qt_app: QApplication,
    workspace: Path,
) -> None:
    """Expose lifecycle summaries only in deep mode, never private reasoning."""

    deep_worker = AgentWorker(provider=_read_provider(), mode="goal")
    deep_spy = QSignalSpy(deep_worker.progress_signal)
    deep_worker.start_agent("读取文件", workspace, max_steps=4, interactive=False)
    assert deep_worker.wait(2_000) is True
    qt_app.processEvents()
    levels = [int(deep_spy.at(index)[0]) for index in range(deep_spy.count())]
    summaries = [str(deep_spy.at(index)[1]) for index in range(deep_spy.count())]
    assert len(summaries) >= 4
    assert any("read_file" in summary for summary in summaries)
    assert 1 in levels
    assert 2 in levels
    assert all("reasoning_content" not in summary for summary in summaries)

    quick_worker = AgentWorker(provider=_read_provider(), mode="auto")
    quick_spy = QSignalSpy(quick_worker.progress_signal)
    quick_worker.start_agent("读取文件", workspace, max_steps=4, interactive=False)
    assert quick_worker.wait(2_000) is True
    qt_app.processEvents()
    assert quick_spy.count() == 0


def test_worker_emits_diff_counts_for_real_interactive_write(
    qt_app: QApplication,
    workspace: Path,
) -> None:
    """Approve one real write and expose the pre-write Unified Diff."""

    target = workspace / "calc.py"
    new_content = (
        "def divide(a: float, b: float) -> float:\n"
        "    if b == 0:\n"
        "        raise ValueError('zero')\n"
        "    return a / b\n"
    )
    requested_paths: list[str] = []

    def approve(path: str) -> bool:
        """Approve the injected confirmation without opening a dialog."""

        requested_paths.append(path)
        return True

    worker = AgentWorker(
        provider=_write_provider(target, new_content),
        confirmation_callback=approve,
    )
    diff_spy = QSignalSpy(worker.diff_signal)

    worker.start_agent("修复除零", workspace, max_steps=4, interactive=True)
    assert worker.wait(2_000) is True
    qt_app.processEvents()

    assert requested_paths == ["calc.py"]
    assert target.read_text(encoding="utf-8") == new_content
    assert diff_spy.count() == 1
    assert diff_spy.at(0)[0] == "calc.py"
    assert "+    if b == 0:" in diff_spy.at(0)[1]
    assert diff_spy.at(0)[2:] == [2, 0]


def test_send_clears_input_and_binds_real_agent(
    qt_app: QApplication,
    workspace: Path,
    gui_settings: QSettings,
) -> None:
    """Clear immediately, show code, and keep only one compact tool line."""

    provider = _read_provider()

    class BoundWindow(MainWindow):
        """Inject a deterministic provider at the production worker boundary."""

        def _create_worker(self, task: str) -> AgentWorker:
            """Return a real worker backed by the local FakeProvider."""

            _ = task
            return AgentWorker(provider=provider, mode=self.mode)

    window = BoundWindow(workspace, settings=gui_settings)
    try:
        window.task_input.setText("读取 calc.py")
        window.send_button.click()
        assert window.task_input.text() == ""
        assert window.worker is not None
        assert window.worker.wait(2_000) is True
        qt_app.processEvents()

        assert window.log_view.toPlainText() == ""
        assert window.tool_status_button.text() == "✅ read_file"
        assert len(window.session_store.active.logs) == 1
        assert window.session_store.active.logs[0]["label"] == "read_file"
        assert "思考" not in window.log_view.toPlainText()
        assert "模型" not in window.log_view.toPlainText()
        assert window.code_tabs.count() == 0
        assert window.code_view.toPlainText() == ""
        assert "读取 calc.py" in window.conversation_view.toPlainText()
        assert window.send_button.isEnabled() is True
        assert "就绪" in window.status_indicator.text()
    finally:
        window.close()
        qt_app.processEvents()


def test_tool_status_is_one_line_and_error_opens_full_detail(
    qt_app: QApplication,
    workspace: Path,
    gui_settings: QSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Overwrite the one-line tool state and reveal failure detail on click."""

    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        lambda parent, title, message: captured.append((title, message)),
    )
    window = MainWindow(workspace, settings=gui_settings)
    try:
        assert window.tool_status_button.height() == 30
        window.update_tool_status("running", "read_file", "")
        assert window.tool_status_button.text() == "🔧 read_file"
        assert window.tool_status_button.isEnabled() is False
        window.update_tool_status("success", "read_file", "")
        assert window.tool_status_button.text() == "✅ read_file"
        window.update_tool_status(
            "error",
            "run_command",
            '{"error": {"code": "permission_denied", "message": "权限不足"}}',
        )
        assert window.tool_status_button.text().startswith("❌ run_command")
        assert "\n" not in window.tool_status_button.text()
        assert window.tool_status_button.isEnabled() is True
        window.tool_status_button.click()
        assert captured == [
            (
                "run_command 执行失败",
                '{"error": {"code": "permission_denied", "message": "权限不足"}}',
            )
        ]
    finally:
        window.close()
        qt_app.processEvents()


def test_deep_mode_shows_process_and_native_loading_feedback(
    qt_app: QApplication,
    workspace: Path,
    gui_settings: QSettings,
) -> None:
    """Show immediate animation and incremental summaries only for deep mode."""

    provider = _read_provider()

    class DeepWindow(MainWindow):
        """Inject a deterministic deep-mode provider."""

        def _create_worker(self, task: str) -> AgentWorker:
            """Bind the selected core mode to the worker."""

            _ = task
            return AgentWorker(provider=provider, mode=self.mode)

    window = DeepWindow(workspace, settings=gui_settings)
    try:
        window.show()
        deep_index = window.thinking_mode_combo.findData("deep")
        window.thinking_mode_combo.setCurrentIndex(deep_index)
        assert window.mode == "goal"
        window.task_input.setText("读取 calc.py")
        window.send_button.click()
        assert window.loading_container.isVisible() is True
        assert window.loading_bar.minimum() == 0
        assert window.loading_bar.maximum() == 0
        assert window.worker is not None
        assert window.worker.wait(2_000) is True
        qt_app.processEvents()

        assert window.loading_container.isVisible() is False
        assert window.thinking_container.isVisible() is True
        assert window.thinking_view.isVisible() is False
        assert window.thinking_container.parent().objectName() == "conversationPanel"
        window.thinking_toggle.click()
        assert window.thinking_view.isVisible() is True
        process_text = window.thinking_view.toPlainText()
        assert "分析任务目标" in process_text
        assert "read_file" in process_text
        assert gui_settings.value("ui/thinking_mode") == "deep"
    finally:
        window.close()
        qt_app.processEvents()


@pytest.mark.parametrize("confirmed", [True, False])
def test_window_interactive_buttons_release_write_and_clear_rejected_preview(
    qt_app: QApplication,
    workspace: Path,
    gui_settings: QSettings,
    confirmed: bool,
) -> None:
    """Block on the Diff buttons; rejection clears its tab and preserves disk."""

    target = workspace / "calc.py"
    original = target.read_text(encoding="utf-8")
    new_content = original.replace("return a / b", "return 0 if b == 0 else a / b")
    provider = _write_provider(target, new_content)

    class InteractiveWindow(MainWindow):
        """Bind an interactive deterministic write provider."""

        def _create_worker(self, task: str) -> AgentWorker:
            """Return the provider-backed worker."""

            _ = task
            return AgentWorker(provider=provider, mode=self.mode)

    window = InteractiveWindow(workspace, settings=gui_settings)
    try:
        window.show()
        qt_app.processEvents()
        assert window.decision_widget.isVisible() is False
        window.task_input.setText("修改 calc.py")
        window.send_button.click()
        assert _wait_until(qt_app, lambda: window._awaiting_confirmation)
        assert "需要您确认" in window.conversation_view.toPlainText()
        assert window.waiting_indicator.isVisible() is True
        assert "Unified Diff" in window.code_view.toPlainText()
        assert window.decision_widget.isVisible() is True

        if confirmed:
            window.apply_button.click()
        else:
            window.reject_button.click()
            assert window.code_view.toPlainText() == original
        assert window.decision_widget.isVisible() is False
        assert "Unified Diff" not in window.code_view.toPlainText()

        assert window.worker is not None
        assert window.worker.wait(2_000) is True
        qt_app.processEvents()
        expected = new_content if confirmed else original
        assert target.read_text(encoding="utf-8") == expected
        if confirmed:
            assert "return 0 if b == 0" in window.code_view.toPlainText()
            assert (
                window.theme_colors["success"].lstrip("#").casefold()
                not in window.code_view.toHtml().casefold()
            )
            window._toggle_theme()
            assert (
                window.theme_colors["success"].lstrip("#").casefold()
                not in window.code_view.toHtml().casefold()
            )
        resolved_text = "已允许修改" if confirmed else "已拒绝修改"
        assert resolved_text in window.conversation_view.toPlainText()
        assert window.waiting_indicator.isVisible() is False
        if confirmed:
            assert "[1] 📝 modified calc.py (+1 -1)" in window.log_view.toPlainText()
        else:
            assert "modified calc.py" not in window.log_view.toPlainText()
        expected_icon = "✅" if confirmed else "↩"
        assert window.tool_status_button.text().startswith(expected_icon)
        assert window.decision_widget.isVisible() is False
    finally:
        if window.worker is not None and window.worker.isRunning():
            window.reject_button.click()
            window.worker.wait(2_000)
        window.close()
        qt_app.processEvents()


@pytest.mark.parametrize("confirmed", [True, False])
def test_new_agent_file_also_requires_diff_approval(
    qt_app: QApplication,
    workspace: Path,
    gui_settings: QSettings,
    confirmed: bool,
) -> None:
    """Stage a new file in memory and create it only after explicit approval."""

    relative_path = "generated.py"
    content = "answer = 42\n"
    target = workspace / relative_path
    provider = _new_file_write_provider(relative_path, content)

    class NewFileWindow(MainWindow):
        def _create_worker(self, task: str) -> AgentWorker:
            _ = task
            return AgentWorker(provider=provider, mode=self.mode)

    window = NewFileWindow(workspace, settings=gui_settings)
    try:
        window.show()
        window.task_input.setText("创建 generated.py")
        window.send_button.click()
        assert _wait_until(qt_app, lambda: window._awaiting_confirmation)
        assert target.exists() is False
        assert "Unified Diff" in window.code_view.toPlainText()
        assert window.apply_button.isEnabled() is True
        assert window.reject_button.isEnabled() is True

        if confirmed:
            window.apply_button.click()
        else:
            window.reject_button.click()
        assert window.worker is not None
        assert window.worker.wait(2_000) is True
        qt_app.processEvents()

        assert target.exists() is confirmed
        if confirmed:
            assert target.read_text(encoding="utf-8") == content
            assert window.code_view.isReadOnly() is False
        else:
            assert window.code_view.toPlainText() == ""
    finally:
        if window.worker is not None and window.worker.isRunning():
            window.confirm_signal.emit(False)
            window.worker.wait(2_000)
        window.close()
        qt_app.processEvents()


def test_manual_code_edit_requires_explicit_save_and_has_no_agent_diff(
    qt_app: QApplication,
    workspace: Path,
    gui_settings: QSettings,
) -> None:
    """Keep user edits separate from the Agent Diff-approval workflow."""

    target = workspace / "calc.py"
    original = target.read_text(encoding="utf-8")
    edited = original.replace("return a / b", "return float('inf') if b == 0 else a / b")
    window = MainWindow(workspace, settings=gui_settings)
    try:
        window.update_code("calc.py", original)
        assert window.code_view.isReadOnly() is False
        window.code_view.setPlainText(edited)
        qt_app.processEvents()

        assert target.read_text(encoding="utf-8") == original
        assert window.manual_save_button.isEnabled() is True
        assert window.code_tabs.tabText(window.code_tabs.currentIndex()).endswith("*")
        assert window.decision_widget.isHidden() is True
        assert "Unified Diff" not in window.code_view.toPlainText()

        window.manual_save_button.click()
        assert target.read_text(encoding="utf-8") == edited
        assert window.manual_save_button.isEnabled() is False
        assert not window.code_tabs.tabText(window.code_tabs.currentIndex()).endswith("*")
        assert "已手动保存" in window.statusBar().currentMessage()
    finally:
        window.close()
        qt_app.processEvents()


def test_pending_diff_tab_close_requires_discard_and_never_writes(
    qt_app: QApplication,
    workspace: Path,
    gui_settings: QSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep a staged Diff in memory until tab-close discard is confirmed."""

    target = workspace / "calc.py"
    original = target.read_text(encoding="utf-8")
    proposed = original.replace("return a / b", "return a * b")
    provider = _write_provider(target, proposed)

    class PendingWindow(MainWindow):
        """Inject one pending interactive write."""

        def _create_worker(self, task: str) -> AgentWorker:
            """Return the deterministic write worker."""

            _ = task
            return AgentWorker(provider=provider, mode=self.mode)

    window = PendingWindow(workspace, settings=gui_settings)
    try:
        window.show()
        window.task_input.setText("暂存修改")
        window.send_button.click()
        assert _wait_until(qt_app, lambda: window._awaiting_confirmation)
        assert target.read_text(encoding="utf-8") == original
        assert window._tab_has_pending_diff("calc.py") is True

        monkeypatch.setattr(window, "_confirm_discard_pending", lambda scope: False)
        window._close_code_tab(window.code_tabs.currentIndex())
        assert window.code_tabs.count() == 1
        assert window._tab_has_pending_diff("calc.py") is True
        assert "Unified Diff" in window.code_view.toPlainText()

        monkeypatch.setattr(window, "_confirm_discard_pending", lambda scope: True)
        window._close_code_tab(window.code_tabs.currentIndex())
        assert window.code_tabs.count() == 0
        assert window.worker is not None
        assert window.worker.wait(2_000) is True
        qt_app.processEvents()
        assert target.read_text(encoding="utf-8") == original
    finally:
        if window.worker is not None and window.worker.isRunning():
            window.confirm_signal.emit(False)
            window.worker.wait(2_000)
        window.close()
        qt_app.processEvents()


def test_pending_diff_window_close_can_cancel_or_discard(
    qt_app: QApplication,
    workspace: Path,
    gui_settings: QSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancel app exit or discard the staged modification before closing."""

    target = workspace / "calc.py"
    original = target.read_text(encoding="utf-8")
    provider = _write_provider(target, original.replace("a / b", "a + b"))

    class PendingWindow(MainWindow):
        """Inject one pending interactive write."""

        def _create_worker(self, task: str) -> AgentWorker:
            """Return the deterministic write worker."""

            _ = task
            return AgentWorker(provider=provider, mode=self.mode)

    window = PendingWindow(workspace, settings=gui_settings)
    window.show()
    window.task_input.setText("退出前确认")
    window.send_button.click()
    assert _wait_until(qt_app, lambda: window._awaiting_confirmation)

    monkeypatch.setattr(window, "_confirm_discard_pending", lambda scope: False)
    window.close()
    qt_app.processEvents()
    assert window.isVisible() is True
    assert window._tab_has_pending_diff("calc.py") is True

    monkeypatch.setattr(window, "_confirm_discard_pending", lambda scope: True)
    window.close()
    assert window.worker is not None
    assert window.worker.wait(2_000) is True
    assert _wait_until(qt_app, lambda: not window.isVisible())
    assert target.read_text(encoding="utf-8") == original


def test_conversations_switch_in_memory_and_restart_starts_fresh(
    qt_app: QApplication,
    workspace: Path,
    gui_settings: QSettings,
) -> None:
    """Keep in-run sessions independent but reset all conversation UI on restart."""

    window = MainWindow(workspace, settings=gui_settings)
    first_id = window.session_store.active_id
    first_task = "A" * 25
    window.session_store.add_message("user", first_task)
    window.update_log(1, "🔧", "read_file", "tool_success", "")
    window._refresh_session_combo()
    window._render_active_session()
    assert window.session_store.active.title == first_task[:20]

    window._new_session()
    second_id = window.session_store.active_id
    window.session_store.add_message("user", "任务 B")
    window.update_log(2, "🔧", "run_command", "tool_success", "")
    window._refresh_session_combo()
    window._render_active_session()
    assert window.session_store.active.logs[0]["label"] == "run_command"
    assert window.log_view.toPlainText() == ""

    first_index = window.session_combo.findData(first_id)
    window.session_combo.setCurrentIndex(first_index)
    assert window.session_store.active.logs[0]["label"] == "read_file"
    assert first_task in window.conversation_view.toPlainText()
    window.close()
    qt_app.processEvents()

    restored = MainWindow(workspace, settings=gui_settings)
    try:
        assert len(restored.session_store.conversations) == 1
        assert restored.session_store.active.messages == []
        assert restored.session_store.active.logs == []
        assert restored.conversation_view.toPlainText() == ""
        assert restored.log_view.toPlainText() == ""
        assert restored.session_store.get(first_id) is None
        assert restored.session_store.get(second_id) is None
    finally:
        restored.close()
        qt_app.processEvents()


def test_message_delete_updates_memory_rendering_and_persisted_store(
    qt_app: QApplication,
    workspace: Path,
    gui_settings: QSettings,
) -> None:
    """Delete one bubble permanently from UI, memory, and QSettings."""

    window = MainWindow(workspace, settings=gui_settings)
    conversation_id = window.session_store.active_id
    window.session_store.add_message("user", "需要删除的消息")
    window.session_store.add_message("assistant", "保留的回复")
    window._refresh_session_combo()
    window._render_active_session()
    assert "需要删除的消息" in window.conversation_view.toPlainText()
    assert [bubble.role for bubble in window.conversation_view.bubbles] == [
        "user",
        "assistant",
    ]
    assert window.conversation_view.bubbles[0].findChild(
        object,
        "userBubble",
    ) is not None
    assert window.conversation_view.bubbles[1].findChild(
        object,
        "assistantBubble",
    ) is not None

    window._delete_conversation_message(conversation_id, 0)
    assert "需要删除的消息" not in window.conversation_view.toPlainText()
    assert "保留的回复" in window.conversation_view.toPlainText()
    assert len(window.session_store.active.messages) == 1
    window.session_store.save()

    reloaded_store = ConversationStore(gui_settings)
    persisted = reloaded_store.get(conversation_id)
    assert persisted is not None
    assert [message["content"] for message in persisted.messages] == ["保留的回复"]
    window.close()
    qt_app.processEvents()


def test_delete_history_conversation_removes_the_entire_session(
    qt_app: QApplication,
    workspace: Path,
    gui_settings: QSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Delete a complete selected conversation, not merely one message."""

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    window = MainWindow(workspace, settings=gui_settings)
    try:
        first_id = window.session_store.active_id
        window.session_store.add_message("user", "第一段历史")
        window._new_session()
        second_id = window.session_store.active_id
        window.session_store.add_message("user", "第二段历史")
        window._refresh_session_combo()
        window._render_active_session()
        assert window.session_combo.count() == 2

        window.delete_session_button.click()
        assert window.session_store.get(second_id) is None
        assert window.session_store.get(first_id) is not None
        assert window.session_combo.count() == 1
        assert "第一段历史" in window.conversation_view.toPlainText()

        window.delete_session_button.click()
        assert window.session_store.get(first_id) is None
        assert window.session_combo.count() == 1
        assert window.session_store.active.messages == []
    finally:
        window.close()
        qt_app.processEvents()


def test_application_without_explicit_workspace_starts_empty(
    qt_app: QApplication,
    gui_settings: QSettings,
) -> None:
    """Ignore stale conversations and show an empty workspace on startup."""

    gui_settings.setValue(
        ConversationStore.SETTINGS_KEY,
        json.dumps(
            [
                {
                    "id": "old-session",
                    "title": "旧任务",
                    "messages": [{"role": "user", "content": "旧内容"}],
                    "logs": [{"step": 1, "label": "read_file"}],
                    "process": [],
                }
            ],
            ensure_ascii=False,
        ),
    )
    gui_settings.setValue(ConversationStore.ACTIVE_KEY, "old-session")
    window = MainWindow(settings=gui_settings)
    try:
        assert window.workspace_root is None
        assert window.workspace_files == set()
        assert window.workspace_tree.isHidden() is True
        assert window.workspace_empty_label.isHidden() is False
        assert window.session_store.active.messages == []
        assert window.conversation_view.toPlainText() == ""
    finally:
        window.close()
        qt_app.processEvents()


def test_workspace_panel_collapses_restores_and_code_tabs_are_independent(
    qt_app: QApplication,
    workspace: Path,
    gui_settings: QSettings,
) -> None:
    """Restore the file panel and switch or close independent file tabs."""

    window = MainWindow(workspace, settings=gui_settings)
    try:
        window.show()
        qt_app.processEvents()
        window.splitter.setSizes([240, 600, 440])
        qt_app.processEvents()
        window._toggle_workspace_panel()
        assert window.workspace_panel.isVisible() is False
        window._toggle_workspace_panel()
        assert window.workspace_panel.isVisible() is True
        assert 120 <= window.workspace_panel.width() <= 400

        assert window.code_stack.currentWidget() is window.code_empty_page
        window.update_code("111.cpp", "int one = 1;\n")
        assert window.code_tabs.count() == 1
        assert window.code_tabs.tabText(0) == "111.cpp"
        assert window.code_tabs.tabBar().isHidden() is False
        assert window.code_stack.currentWidget() is window.code_tabs
        assert window.code_stack.indexOf(window.code_tabs) >= 0
        window.update_code("leet.cpp", "int two = 2;\n")
        assert window.code_tabs.count() == 2
        first_index = next(
            index
            for index in range(window.code_tabs.count())
            if window.code_tabs.tabText(index) == "111.cpp"
        )
        window.code_tabs.setCurrentIndex(first_index)
        assert "int one = 1" in window.code_view.toPlainText()
        window._close_code_tab(first_index)
        assert window.code_tabs.count() == 1
        assert "int two = 2" in window.code_view.toPlainText()
        window._close_code_tab(0)
        assert window.code_tabs.count() == 0
        assert window.code_stack.currentWidget() is window.code_empty_page
        assert window.code_stack.indexOf(window.code_tabs) == -1
    finally:
        window.close()
        qt_app.processEvents()


def test_drop_no_previews_only_and_yes_imports_into_workspace(
    qt_app: QApplication,
    workspace: Path,
    gui_settings: QSettings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Route rejected and accepted drops to preview-only and import paths."""

    preview_source = tmp_path / "preview.cpp"
    preview_source.write_text("int preview = 1;\n", encoding="utf-8")
    import_source = tmp_path / "imported.cpp"
    import_source.write_text("int imported = 2;\n", encoding="utf-8")
    decisions = iter(
        [QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes]
    )
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: next(decisions))

    window = MainWindow(workspace, settings=gui_settings)
    try:
        preview_event = FakeDropEvent(preview_source)
        window.dropEvent(preview_event)  # type: ignore[arg-type]
        assert preview_event.accepted is True
        assert "preview.cpp" not in window.workspace_files
        assert not (workspace / "preview.cpp").exists()
        assert "int preview = 1" in window.code_view.toPlainText()

        window._toggle_workspace_panel()
        assert window._workspace_collapsed is True
        import_event = FakeDropEvent(import_source)
        window.dropEvent(import_event)  # type: ignore[arg-type]
        assert import_event.accepted is True
        assert "imported.cpp" in window.workspace_files
        assert (workspace / "imported.cpp").read_text(encoding="utf-8") == "int imported = 2;\n"
        assert window._workspace_collapsed is False
        assert "int imported = 2" in window.code_view.toPlainText()
    finally:
        window.close()
        qt_app.processEvents()


def test_snapshot_overwrites_one_slot_and_rollback_reports_timestamp(
    qt_app: QApplication,
    workspace: Path,
    gui_settings: QSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replace the old backup, restore the latest state, and show its time."""

    target = workspace / "calc.py"
    window = MainWindow(workspace, settings=gui_settings)
    timestamps = iter(["2026-08-28 14:30:24", "2026-08-28 14:30:25"])
    monkeypatch.setattr(window, "_now_timestamp", lambda: next(timestamps))
    try:
        window._save_snapshot()
        old_backup = Path(window.snapshot_data[SNAPSHOT_META_KEY])
        target.write_text("version two\n", encoding="utf-8")
        window._save_snapshot()
        assert old_backup.exists() is False
        assert window.snapshot_timestamp == "2026-08-28 14:30:25"
        assert "2026-08-28 14:30:25" in window.snapshot_label.text()

        target.write_text("version three\n", encoding="utf-8")
        window._rollback_snapshot()
        assert target.read_text(encoding="utf-8") == "version two\n"
        assert "[2026-08-28 14:30:25]" in window.statusBar().currentMessage()
    finally:
        window.close()
        qt_app.processEvents()


def test_theme_selection_persists_across_window_restart(
    qt_app: QApplication,
    workspace: Path,
    gui_settings: QSettings,
) -> None:
    """Apply the light theme globally and restore the saved preference."""

    window = MainWindow(workspace, settings=gui_settings)
    assert window.theme_name == "dark"
    window._toggle_theme()
    assert window.theme_name == "light"
    assert gui_settings.value("ui/theme") == "light"
    window.close()
    qt_app.processEvents()

    restored = MainWindow(workspace, settings=gui_settings)
    try:
        assert restored.theme_name == "light"
        assert "暗色" in restored.theme_button.text()
    finally:
        restored.close()
        qt_app.processEvents()


def test_gui_flag_forwards_optional_workspace_without_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Launch GUI routing before Provider validation and forward its workspace."""

    received_workspace: Path | None = None

    def fake_run_gui(workspace_root: Path | None = None) -> int:
        """Record GUI dispatch without starting a Qt event loop."""

        nonlocal received_workspace
        received_workspace = workspace_root
        return 0

    monkeypatch.setattr(main_module, "run_gui", fake_run_gui)

    assert main_module.main(["--gui", "--workspace", str(tmp_path)]) == 0
    assert received_workspace == tmp_path


def test_multiple_agent_reads_never_open_code_tabs(
    qt_app: QApplication,
    workspace: Path,
) -> None:
    """Keep repeated model context reads out of the user's editor workspace."""

    provider = FakeProvider(
        [
            _tool_turn(
                f"read-{index}",
                "read_file",
                {
                    "path": "calc.py",
                    "start_line": index,
                    "max_lines": 1,
                    "max_chars": 1_000,
                },
            )
            for index in range(1, 4)
        ]
        + [_final_turn()]
    )
    worker = AgentWorker(provider=provider)
    code_spy = QSignalSpy(worker.code_signal)
    diff_spy = QSignalSpy(worker.diff_signal)

    worker.start_agent("连续读取", workspace, max_steps=6, interactive=False)
    assert worker.wait(2_000) is True
    qt_app.processEvents()
    assert code_spy.count() == 0
    assert diff_spy.count() == 0


def test_message_bubbles_use_at_least_80_percent_of_conversation_width(
    qt_app: QApplication,
    workspace: Path,
    gui_settings: QSettings,
) -> None:
    """Use the conversation width responsively without creating wide text walls."""

    window = MainWindow(workspace, settings=gui_settings)
    try:
        window.show()
        window.session_store.add_message("assistant", "word " * 40)
        window._render_active_session()
        window.conversation_view.resize(700, 400)
        qt_app.processEvents()
        bubble = window.conversation_view.bubbles[0].bubble_frame
        expected = int(window.conversation_view.viewport().width() * 0.85)
        assert bubble.maximumWidth() == max(80, expected)
        assert bubble.minimumWidth() >= int(
            window.conversation_view.viewport().width() * 0.80
        )

        window.conversation_view.resize(1_200, 400)
        qt_app.processEvents()
        expected_wide = int(window.conversation_view.viewport().width() * 0.85)
        assert bubble.maximumWidth() == expected_wide
        assert bubble.width() >= int(window.conversation_view.viewport().width() * 0.80)
        content_label = bubble.findChild(QLabel, "messageContent")
        assert content_label is not None
        line_height = max(1, content_label.fontMetrics().lineSpacing())
        wrapped_rect = content_label.fontMetrics().boundingRect(
            0,
            0,
            content_label.width(),
            10_000,
            int(Qt.TextFlag.TextWordWrap),
            content_label.text(),
        )
        assert wrapped_rect.height() / line_height <= 3.5
    finally:
        window.close()
        qt_app.processEvents()


def test_ctrl_s_saves_manual_edits_and_close_warns_when_dirty(
    qt_app: QApplication,
    workspace: Path,
    gui_settings: QSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bind Ctrl+S to the editor and protect unsaved text on application exit."""

    target = workspace / "calc.py"
    original = target.read_text(encoding="utf-8")
    window = MainWindow(workspace, settings=gui_settings)
    window.show()
    window.update_code("calc.py", original)
    window.code_view.setPlainText(original + "# saved with shortcut\n")
    assert window.save_file_action.shortcut() == QKeySequence("Ctrl+S")
    window.save_file_action.trigger()
    qt_app.processEvents()
    assert target.read_text(encoding="utf-8").endswith("# saved with shortcut\n")
    assert window.save_file_action.isEnabled() is False

    window.code_view.setPlainText(window.code_view.toPlainText() + "# dirty\n")
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.No,
    )
    window.close()
    qt_app.processEvents()
    assert window.isVisible() is True

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    window.close()
    qt_app.processEvents()
    assert window.isVisible() is False


class BlockingFinalProvider(ModelProvider):
    """Hold one model request until a GUI-session assertion has been made."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def complete(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
    ) -> AssistantTurn:
        """Wait deterministically and then return a final answer."""

        _ = (messages, tools)
        self.entered.set()
        self.release.wait(timeout=2.0)
        return _final_turn("后台会话已完成。")


def test_new_session_does_not_inherit_background_run_ui_or_answer(
    qt_app: QApplication,
    workspace: Path,
    gui_settings: QSettings,
) -> None:
    """Let the old task finish in its owner session while a new chat stays empty."""

    provider = BlockingFinalProvider()

    class BlockingWindow(MainWindow):
        def _create_worker(self, task: str) -> AgentWorker:
            _ = task
            return AgentWorker(provider=provider, mode=self.mode)

    window = BlockingWindow(workspace, settings=gui_settings)
    try:
        old_id = window.session_store.active_id
        window.task_input.setText("旧会话任务")
        window.send_button.click()
        assert provider.entered.wait(timeout=1.0)
        assert window.loading_container.isHidden() is False

        window._new_session()
        new_id = window.session_store.active_id
        assert new_id != old_id
        assert window.conversation_view.toPlainText() == ""
        assert window.loading_container.isHidden() is True

        provider.release.set()
        assert window.worker is not None
        assert window.worker.wait(2_000) is True
        qt_app.processEvents()
        assert window.session_store.active_id == new_id
        assert window.conversation_view.toPlainText() == ""
        old_session = window.session_store.get(old_id)
        assert old_session is not None
        assert any(
            "后台会话已完成" in str(item.get("content"))
            for item in old_session.messages
        )
    finally:
        provider.release.set()
        if window.worker is not None and window.worker.isRunning():
            window.worker.stop()
            window.worker.wait(2_000)
        window.close()
        qt_app.processEvents()


def test_send_button_becomes_stop_and_preserves_partial_session(
    qt_app: QApplication,
    workspace: Path,
    gui_settings: QSettings,
) -> None:
    """Stop cooperatively and keep the existing user message plus stop summary."""

    provider = BlockingFinalProvider()

    class BlockingWindow(MainWindow):
        def _create_worker(self, task: str) -> AgentWorker:
            _ = task
            return AgentWorker(provider=provider, mode=self.mode)

    window = BlockingWindow(workspace, settings=gui_settings)
    try:
        window.task_input.setText("可以停止的任务")
        window.send_button.click()
        assert provider.entered.wait(timeout=1.0)
        qt_app.processEvents()
        assert window.send_button.text() == ""
        assert window.send_button.icon().isNull() is False
        assert window.send_button.property("stopMode") is True

        stop_started = time.monotonic()
        window.send_button.click()
        provider.release.set()
        assert window.worker is not None
        assert window.worker.wait(2_000) is True
        qt_app.processEvents()
        assert time.monotonic() - stop_started < 2.0
        assert window.send_button.text() == "发送"
        assert "可以停止的任务" in window.conversation_view.toPlainText()
        assert "已按用户请求停止" in window.conversation_view.toPlainText()
        assert "错误" not in window.status_indicator.text()
        assert window.statusBar().currentMessage() == "已停止"
    finally:
        provider.release.set()
        if window.worker is not None and window.worker.isRunning():
            window.worker.stop()
            window.worker.wait(2_000)
        window.close()
        qt_app.processEvents()


def test_toolbar_status_cards_use_a_distinct_surface() -> None:
    """Keep toolbar status labels visually separate from their toolbar surface."""

    assert DARK_COLORS["toolbar_card"] != DARK_COLORS["panel"]
    assert LIGHT_COLORS["toolbar_card"] != LIGHT_COLORS["panel"]
    for selector in ("statusIndicator", "workspaceLabel", "snapshotLabel"):
        assert f"QLabel#{selector}" in DARK_THEME


def test_deep_mode_shows_native_reasoning_as_collapsed_narrative(
    qt_app: QApplication,
    workspace: Path,
    gui_settings: QSettings,
) -> None:
    """Render provider reasoning continuously in deep mode and keep it collapsed."""

    reasoning = "先检查目标文件。\n然后验证修改不会破坏现有行为。"
    turn = AssistantTurn(
        content="分析完成。",
        tool_calls=[],
        protocol_message={
            "role": "assistant",
            "content": "分析完成。",
            "reasoning_content": reasoning,
        },
        finish_reason="stop",
    )
    provider = FakeProvider([turn])

    class ReasoningWindow(MainWindow):
        def _create_worker(self, task: str) -> AgentWorker:
            _ = task
            return AgentWorker(provider=provider, mode=self.mode)

    window = ReasoningWindow(workspace, settings=gui_settings)
    try:
        deep_index = window.thinking_mode_combo.findData("deep")
        window.thinking_mode_combo.setCurrentIndex(deep_index)
        window.task_input.setText("深度分析")
        window.send_button.click()
        assert window.worker is not None
        assert window.worker.wait(2_000) is True
        qt_app.processEvents()

        assert window.session_store.active.reasoning == reasoning
        assert window.thinking_container.isHidden() is False
        assert window.thinking_view.isHidden() is True
        window.thinking_toggle.click()
        assert window.thinking_view.toPlainText() == reasoning
        assert "1." not in window.thinking_view.toPlainText()
    finally:
        window.close()
        qt_app.processEvents()


def test_file_mentions_filter_insert_and_expand_workplace(
    qt_app: QApplication,
    workspace: Path,
    gui_settings: QSettings,
) -> None:
    """Offer fuzzy file mentions and expand @workplace into paths plus sizes."""

    nested = workspace / "src"
    nested.mkdir()
    (nested / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    window = MainWindow(workspace, settings=gui_settings)
    try:
        window.show()
        window.task_input.setText("检查 @hel")
        qt_app.processEvents()
        assert window.file_mention_popup.isVisible() is True
        listed = [
            window.file_mention_popup.list_widget.item(index).data(
                Qt.ItemDataRole.UserRole
            )
            for index in range(window.file_mention_popup.list_widget.count())
        ]
        assert "src/helper.py" in listed
        helper_row = listed.index("src/helper.py")
        window.file_mention_popup.list_widget.setCurrentRow(helper_row)
        assert window.file_mention_popup.choose_current() is True
        assert "@src/helper.py" in window.task_input.text()

        window.task_input.setText("概览 @workplace")
        qt_app.processEvents()
        text = window.task_input.text()
        assert "@workplace" not in text
        assert "[工作区文件:" in text
        assert "calc.py (" in text
        assert "src/helper.py (" in text
        assert "bytes" in text
    finally:
        window.file_mention_popup.hide()
        window.close()
        qt_app.processEvents()
