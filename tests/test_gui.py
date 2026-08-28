"""Headless regression tests for the PySide6 desktop interface."""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable, Generator, Sequence
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QMimeData, QSettings, Qt, QUrl
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMessageBox

import main as main_module
from agent.state import AssistantTurn, ToolCall
from gui.main_window import MainWindow
from gui.theme import DARK_THEME, LIGHT_THEME
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
    """Provide complete global QSS for both selectable themes."""

    for color in (
        "#1e1e2e",
        "#11111b",
        "#181825",
        "#cdd6f4",
        "#6c7086",
        "#a6e3a1",
        "#f38ba8",
        "#89b4fa",
        "#cba6f7",
        "#f9e2af",
        "#313244",
        "#45475a",
    ):
        assert color in DARK_THEME
    for color in ("#eff1f5", "#e6e9ef", "#4c4f69", "#1e66f5"):
        assert color in LIGHT_THEME


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
        assert str(workspace) in window.workspace_label.text()
        assert "就绪" in window.status_indicator.text()
    finally:
        window.close()
        qt_app.processEvents()


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
    assert code_spy.count() == 1
    assert code_spy.at(0)[0] == "calc.py"
    assert "def divide" in code_spy.at(0)[1]
    assert finished_spy.count() == 1
    assert finished_spy.at(0)[0] is True


def test_two_tools_produce_exactly_two_log_rows(
    qt_app: QApplication,
    workspace: Path,
) -> None:
    """Keep the visible log count equal to completed tool-call count."""

    worker = AgentWorker(provider=_two_tool_provider())
    log_spy = QSignalSpy(worker.log_signal)
    worker.start_agent("读取后运行", workspace, max_steps=5, interactive=False)
    assert worker.wait(4_000) is True
    qt_app.processEvents()

    assert log_spy.count() == 2
    assert [log_spy.at(index)[2] for index in range(2)] == [
        "read_file",
        "run_command",
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
    summaries = [str(deep_spy.at(index)[0]) for index in range(deep_spy.count())]
    assert len(summaries) >= 4
    assert any("read_file" in summary for summary in summaries)
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

        assert window.log_view.toPlainText().splitlines() == ["[1] 🔧 read_file"]
        assert "思考" not in window.log_view.toPlainText()
        assert "模型" not in window.log_view.toPlainText()
        assert "def divide" in window.code_view.toPlainText()
        assert "读取 calc.py" in window.conversation_view.toPlainText()
        assert window.send_button.isEnabled() is True
        assert "就绪" in window.status_indicator.text()
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
        window.interactive_action.setChecked(True)
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
            assert window.code_view.toPlainText() == ""
        assert window.decision_widget.isVisible() is False
        assert "Unified Diff" not in window.code_view.toPlainText()

        assert window.worker is not None
        assert window.worker.wait(2_000) is True
        qt_app.processEvents()
        expected = new_content if confirmed else original
        assert target.read_text(encoding="utf-8") == expected
        if confirmed:
            assert "return 0 if b == 0" in window.code_view.toPlainText()
        resolved_text = "已允许修改" if confirmed else "已拒绝修改"
        assert resolved_text in window.conversation_view.toPlainText()
        assert window.waiting_indicator.isVisible() is False
        lines = window.log_view.toPlainText().splitlines()
        assert len(lines) == 1
        assert ("🔧 write_file" in lines[0]) is confirmed
        assert ("❌ write_file" in lines[0]) is (not confirmed)
    finally:
        if window.worker is not None and window.worker.isRunning():
            window.reject_button.click()
            window.worker.wait(2_000)
        window.close()
        qt_app.processEvents()


def test_conversations_switch_without_losing_logs_and_persist(
    qt_app: QApplication,
    workspace: Path,
    gui_settings: QSettings,
) -> None:
    """Preserve independent messages/logs across switches and restart."""

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
    assert window.log_view.toPlainText() == "[2] 🔧 run_command"

    first_index = window.session_combo.findData(first_id)
    window.session_combo.setCurrentIndex(first_index)
    assert window.log_view.toPlainText() == "[1] 🔧 read_file"
    assert first_task in window.conversation_view.toPlainText()
    window.close()
    qt_app.processEvents()

    restored = MainWindow(workspace, settings=gui_settings)
    try:
        assert len(restored.session_store.conversations) == 2
        first = restored.session_store.get(first_id)
        second = restored.session_store.get(second_id)
        assert first is not None
        assert second is not None
        assert first.logs[0]["label"] == "read_file"
    finally:
        restored.close()
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

        window.update_code("111.cpp", "int one = 1;\n")
        assert window.code_tabs.count() == 1
        assert window.code_tabs.tabText(0) == "111.cpp"
        assert window.code_tabs.tabBar().isHidden() is False
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
