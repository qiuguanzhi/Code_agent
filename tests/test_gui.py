"""Headless tests for the real Phase 5 GUI-to-Agent binding."""

from __future__ import annotations

import json
from collections.abc import Generator, Sequence
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

import main as main_module
from agent.state import AssistantTurn, ToolCall
from gui.main_window import MainWindow
from gui.theme import DARK_THEME
from gui.worker import AgentWorker
from providers.base import ModelProvider
from tools.filesystem import sha256_file_streaming


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


@pytest.fixture(scope="module")
def qt_app() -> Generator[QApplication, None, None]:
    """Provide one offscreen Qt application for widget and signal tests."""

    existing = QApplication.instance()
    app = existing if existing is not None else QApplication([])
    yield app
    app.processEvents()


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


def test_main_window_contains_required_controls(
    qt_app: QApplication,
    workspace: Path,
) -> None:
    """Keep the accepted Phase 4 layout while binding a workspace."""

    window = MainWindow(workspace)
    try:
        assert window.findChild(object, "openWorkspaceAction") is not None
        assert window.findChild(object, "mainToolbar") is not None
        assert window.splitter.orientation() == Qt.Orientation.Horizontal
        assert window.splitter.count() == 2
        assert window.log_view.isReadOnly() is True
        assert window.code_view.isReadOnly() is True
        assert window.apply_button.isEnabled() is False
        assert window.reject_button.isEnabled() is False
        assert str(workspace) in window.workspace_label.text()
        assert "就绪" in window.status_indicator.text()
    finally:
        window.close()
        qt_app.processEvents()


def test_worker_maps_real_read_events_to_log_and_code_signals(
    qt_app: QApplication,
    workspace: Path,
) -> None:
    """Run read_file through run_agent and expose its actual file content."""

    worker = AgentWorker(provider=_read_provider())
    log_spy = QSignalSpy(worker.log_signal)
    code_spy = QSignalSpy(worker.code_signal)
    finished_spy = QSignalSpy(worker.finished_signal)

    worker.start_agent("读取 calc.py", workspace, max_steps=4, interactive=False)
    assert worker.wait(2_000) is True
    qt_app.processEvents()

    assert log_spy.count() >= 8
    assert code_spy.count() == 1
    assert code_spy.at(0)[0] == "calc.py"
    assert "def divide" in code_spy.at(0)[1]
    assert finished_spy.count() == 1
    assert finished_spy.at(0)[0] is True


def test_worker_emits_diff_counts_for_real_interactive_write(
    qt_app: QApplication,
    workspace: Path,
) -> None:
    """Approve one real write and expose the tool-generated Unified Diff."""

    target = workspace / "calc.py"
    new_content = (
        "def divide(a: float, b: float) -> float:\n"
        "    if b == 0:\n"
        "        raise ValueError('zero')\n"
        "    return a / b\n"
    )
    provider = FakeProvider(
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
            _final_turn("修改完成。"),
        ]
    )
    requested_paths: list[str] = []

    def approve(path: str) -> bool:
        """Approve the injected confirmation without opening a dialog."""

        requested_paths.append(path)
        return True

    worker = AgentWorker(provider=provider, confirmation_callback=approve)
    diff_spy = QSignalSpy(worker.diff_signal)

    worker.start_agent("修复除零", workspace, max_steps=4, interactive=True)
    assert worker.wait(2_000) is True
    qt_app.processEvents()

    assert requested_paths == ["calc.py"]
    assert target.read_text(encoding="utf-8") == new_content
    assert diff_spy.count() == 1
    assert diff_spy.at(0)[0] == "calc.py"
    assert "+    if b == 0:" in diff_spy.at(0)[1]
    assert diff_spy.at(0)[2] == 2
    assert diff_spy.at(0)[3] == 0


def test_send_binds_window_to_real_agent_worker(
    qt_app: QApplication,
    workspace: Path,
) -> None:
    """Exercise button → worker → run_agent → code/log UI end to end."""

    provider = _read_provider()

    class BoundWindow(MainWindow):
        """Inject a deterministic provider at the production worker boundary."""

        def _create_worker(self, task: str) -> AgentWorker:
            """Return a real worker backed by the local FakeProvider."""

            _ = task
            return AgentWorker(provider=provider, mode=self.mode)

    window = BoundWindow(workspace)
    try:
        window.task_input.setText("读取 calc.py")
        window.send_button.click()
        assert window.worker is not None
        assert window.worker.wait(2_000) is True
        qt_app.processEvents()

        log_text = window.log_view.toPlainText()
        assert "思考" in log_text
        assert "调用工具：read_file" in log_text
        assert "任务已完成" in log_text
        assert "def divide" in window.code_view.toPlainText()
        assert "calc.py" in window.code_view.toPlainText()
        assert window.send_button.isEnabled() is True
        assert "就绪" in window.status_indicator.text()
    finally:
        window.close()
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
