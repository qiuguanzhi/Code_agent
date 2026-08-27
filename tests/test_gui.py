"""Headless tests for the Phase 4 PySide6 desktop skeleton."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

import main as main_module
from gui.main_window import MainWindow
from gui.theme import DARK_THEME
from gui.worker import AgentWorker


@pytest.fixture(scope="module")
def qt_app() -> Generator[QApplication, None, None]:
    """Provide one offscreen Qt application for widget and signal tests."""

    existing = QApplication.instance()
    app = existing if existing is not None else QApplication([])
    yield app
    app.processEvents()


def test_dark_theme_uses_catppuccin_mocha_palette() -> None:
    """Keep every required design color represented by the shared QSS."""

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


def test_main_window_contains_required_controls(qt_app: QApplication) -> None:
    """Verify menus, toolbars, split columns, inputs, and initial decisions."""

    window = MainWindow()
    try:
        assert window.findChild(object, "openWorkspaceAction") is not None
        assert window.findChild(object, "saveSnapshotAction") is not None
        assert window.findChild(object, "rollbackAction") is not None
        assert window.findChild(object, "mainToolbar") is not None
        assert window.splitter.orientation() == Qt.Orientation.Horizontal
        assert window.splitter.count() == 2
        assert window.log_view.isReadOnly() is True
        assert window.code_view.isReadOnly() is True
        assert window.apply_button.isEnabled() is False
        assert window.reject_button.isEnabled() is False
        assert window.auto_mode_action.isChecked() is True
        assert window.mode_label.text() == "模式：auto"
        assert "就绪" in window.status_indicator.text()
    finally:
        window.close()
        qt_app.processEvents()


def test_worker_emits_six_logs_and_preview_signals(qt_app: QApplication) -> None:
    """Run the worker quickly while preserving its six-event contract."""

    worker = AgentWorker("修复测试", interval_ms=1, total_ticks=10)
    log_spy = QSignalSpy(worker.log_signal)
    code_spy = QSignalSpy(worker.code_signal)
    diff_spy = QSignalSpy(worker.diff_signal)
    finished_spy = QSignalSpy(worker.finished_signal)

    worker.start()
    assert worker.wait(2_000) is True
    qt_app.processEvents()

    assert log_spy.count() == 6
    assert code_spy.count() == 1
    assert diff_spy.count() == 1
    assert finished_spy.count() == 1


def test_send_appends_six_colored_logs(qt_app: QApplication) -> None:
    """Exercise the send-to-worker signal path without a five-second wait."""

    class FastWindow(MainWindow):
        """Use the production window with a test-speed worker factory."""

        def _create_worker(self, task: str) -> AgentWorker:
            """Create a one-millisecond placeholder worker."""

            return AgentWorker(task, interval_ms=1, total_ticks=10)

    window = FastWindow()
    try:
        window.task_input.setText("修复除零错误")
        window.send_button.click()
        assert window.worker is not None
        assert window.worker.wait(2_000) is True
        qt_app.processEvents()

        assert window.log_view.document().blockCount() == 6
        assert "[SUCCESS]" in window.log_view.toPlainText()
        assert "Unified Diff" in window.code_view.toPlainText()
        assert window.send_button.isEnabled() is True
        assert "就绪" in window.status_indicator.text()
    finally:
        window.close()
        qt_app.processEvents()


def test_gui_flag_does_not_require_workspace_or_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Select GUI mode before CLI workspace/provider validation."""

    called = False

    def fake_run_gui() -> int:
        """Record the GUI dispatch without starting an event loop."""

        nonlocal called
        called = True
        return 0

    monkeypatch.setattr(main_module, "run_gui", fake_run_gui)

    assert main_module.main(["--gui"]) == 0
    assert called is True
