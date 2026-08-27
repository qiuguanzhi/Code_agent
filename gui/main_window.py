"""Main window for the Phase 4 PySide6 desktop skeleton."""

from __future__ import annotations

from html import escape
from pathlib import Path

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QFontDatabase, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QSplitter,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from gui.worker import AgentWorker
from utils.snapshot import rollback_to_snapshot, save_workspace_snapshot


STATUS_COLORS: dict[str, str] = {
    "ready": "#a6e3a1",
    "running": "#f9e2af",
    "error": "#f38ba8",
}

LOG_COLORS: dict[str, str] = {
    "info": "#89b4fa",
    "thinking": "#cba6f7",
    "warning": "#f9e2af",
    "success": "#a6e3a1",
    "error": "#f38ba8",
}


class MainWindow(QMainWindow):
    """Display the GUI structure while delegating work to a mock QThread."""

    def __init__(self, workspace_root: Path | None = None) -> None:
        """Initialize the menus, toolbar, split view, input, and status bar."""

        super().__init__()
        self.workspace_root = workspace_root.resolve() if workspace_root is not None else None
        self.snapshot_data: dict[str, str] = {}
        self.mode = "auto"
        self.interactive_confirmation = False
        self.worker: AgentWorker | None = None
        self._latest_code = ""
        self._latest_diff = ""

        self.setWindowTitle("Mini Coding Agent")
        self.resize(1180, 760)
        self.setMinimumSize(860, 560)

        self._create_actions()
        self._create_menu_bar()
        self._create_tool_bar()
        self._create_central_widget()
        self.statusBar().setObjectName("statusBar")
        self._set_status("ready", "就绪")
        self._refresh_workspace_label()

    def _create_actions(self) -> None:
        """Create reusable actions and connect their callbacks."""

        self.open_workspace_action = QAction("打开工作区", self)
        self.open_workspace_action.setObjectName("openWorkspaceAction")
        self.open_workspace_action.setShortcut(QKeySequence("Ctrl+O"))
        self.open_workspace_action.triggered.connect(self._open_workspace)

        self.save_snapshot_action = QAction("保存快照", self)
        self.save_snapshot_action.setObjectName("saveSnapshotAction")
        self.save_snapshot_action.setShortcut(QKeySequence("Ctrl+S"))
        self.save_snapshot_action.triggered.connect(self._save_snapshot)

        self.rollback_action = QAction("回退到快照", self)
        self.rollback_action.setObjectName("rollbackAction")
        self.rollback_action.setShortcut(QKeySequence("Ctrl+R"))
        self.rollback_action.triggered.connect(self._rollback_snapshot)

        self.auto_mode_action = QAction("Auto", self, checkable=True)
        self.auto_mode_action.setObjectName("autoModeAction")
        self.goal_mode_action = QAction("Goal", self, checkable=True)
        self.goal_mode_action.setObjectName("goalModeAction")
        self.mode_action_group = QActionGroup(self)
        self.mode_action_group.setExclusive(True)
        self.mode_action_group.addAction(self.auto_mode_action)
        self.mode_action_group.addAction(self.goal_mode_action)
        self.auto_mode_action.setChecked(True)
        self.auto_mode_action.triggered.connect(lambda checked: self._set_mode("auto", checked))
        self.goal_mode_action.triggered.connect(lambda checked: self._set_mode("goal", checked))

        self.interactive_action = QAction("交互确认", self, checkable=True)
        self.interactive_action.setObjectName("interactiveAction")
        self.interactive_action.toggled.connect(self._set_interactive_confirmation)

        self.run_action = QAction("▶ 运行任务", self)
        self.run_action.setObjectName("runButton")
        self.run_action.triggered.connect(self._submit_task)

        self.toolbar_rollback_action = QAction("↩ 回退", self)
        self.toolbar_rollback_action.setObjectName("toolbarRollbackAction")
        self.toolbar_rollback_action.triggered.connect(self._rollback_snapshot)

    def _create_menu_bar(self) -> None:
        """Populate the File and Settings menus."""

        file_menu = self.menuBar().addMenu("文件")
        file_menu.setObjectName("fileMenu")
        file_menu.addAction(self.open_workspace_action)
        file_menu.addAction(self.save_snapshot_action)
        file_menu.addAction(self.rollback_action)

        settings_menu = self.menuBar().addMenu("设置")
        settings_menu.setObjectName("settingsMenu")
        mode_menu = settings_menu.addMenu("切换模式")
        mode_menu.addAction(self.auto_mode_action)
        mode_menu.addAction(self.goal_mode_action)
        settings_menu.addSeparator()
        settings_menu.addAction(self.interactive_action)

    def _create_tool_bar(self) -> None:
        """Create the primary action and status toolbar."""

        toolbar = QToolBar("主工具栏", self)
        toolbar.setObjectName("mainToolbar")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        toolbar.addAction(self.run_action)
        run_button = toolbar.widgetForAction(self.run_action)
        if run_button is not None:
            run_button.setObjectName("runButton")
        toolbar.addSeparator()

        self.status_indicator = QLabel()
        self.status_indicator.setObjectName("statusIndicator")
        toolbar.addWidget(self.status_indicator)
        toolbar.addSeparator()

        self.workspace_label = QLabel()
        self.workspace_label.setObjectName("workspaceLabel")
        toolbar.addWidget(self.workspace_label)
        toolbar.addSeparator()

        self.mode_label = QLabel("模式：auto")
        self.mode_label.setObjectName("modeLabel")
        toolbar.addWidget(self.mode_label)
        toolbar.addSeparator()
        toolbar.addAction(self.toolbar_rollback_action)

    def _create_central_widget(self) -> None:
        """Build the adjustable log/code columns and bottom task input."""

        root = QWidget(self)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(9)

        self.splitter = QSplitter(Qt.Orientation.Horizontal, root)
        self.splitter.setObjectName("mainSplitter")

        self.log_view = QTextEdit(self.splitter)
        self.log_view.setObjectName("logView")
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("运行任务后，Agent 日志将显示在这里。")
        self.log_view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.splitter.addWidget(self.log_view)

        right_panel = QWidget(self.splitter)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        self.code_view = QTextEdit(right_panel)
        self.code_view.setObjectName("codeView")
        self.code_view.setReadOnly(True)
        self.code_view.setPlaceholderText("候选代码和 Unified Diff 将显示在这里。")
        self.code_view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        right_layout.addWidget(self.code_view)

        decision_layout = QHBoxLayout()
        decision_layout.addStretch(1)
        self.apply_button = QPushButton("应用修改", right_panel)
        self.apply_button.setObjectName("applyButton")
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self._apply_placeholder_change)
        decision_layout.addWidget(self.apply_button)
        self.reject_button = QPushButton("拒绝", right_panel)
        self.reject_button.setObjectName("rejectButton")
        self.reject_button.setEnabled(False)
        self.reject_button.clicked.connect(self._reject_placeholder_change)
        decision_layout.addWidget(self.reject_button)
        right_layout.addLayout(decision_layout)

        self.splitter.addWidget(right_panel)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        self.splitter.setSizes([600, 400])
        root_layout.addWidget(self.splitter, 1)

        input_layout = QHBoxLayout()
        self.task_input = QLineEdit(root)
        self.task_input.setObjectName("taskInput")
        self.task_input.setPlaceholderText("输入编程任务，例如：修复除零错误")
        self.task_input.returnPressed.connect(self._submit_task)
        input_layout.addWidget(self.task_input, 1)
        self.send_button = QPushButton("发送", root)
        self.send_button.setObjectName("sendButton")
        self.send_button.clicked.connect(self._submit_task)
        input_layout.addWidget(self.send_button)
        root_layout.addLayout(input_layout)

        self.setCentralWidget(root)

    def _create_worker(self, task: str) -> AgentWorker:
        """Create the placeholder worker; Phase 5 can replace this boundary."""

        return AgentWorker(task)

    @Slot()
    def _submit_task(self) -> None:
        """Start one mock task if no worker is currently active."""

        task = self.task_input.text().strip()
        if not task:
            self._set_status("error", "请先输入任务")
            return
        if self.worker is not None and self.worker.isRunning():
            self.statusBar().showMessage("已有任务正在运行")
            return

        self.log_view.clear()
        self._latest_code = ""
        self._latest_diff = ""
        self._refresh_code_panel()
        self.apply_button.setEnabled(False)
        self.reject_button.setEnabled(False)
        self.send_button.setEnabled(False)
        self.run_action.setEnabled(False)
        self._set_status("running", "模拟 Agent 正在运行")

        self.worker = self._create_worker(task)
        self.worker.log_signal.connect(self._append_log)
        self.worker.code_signal.connect(self._show_code)
        self.worker.diff_signal.connect(self._show_diff)
        self.worker.status_signal.connect(self._set_status)
        self.worker.finished_signal.connect(self._handle_worker_finished)
        self.worker.start()

    @Slot(str, str)
    def _append_log(self, message: str, level: str = "info") -> None:
        """Append one escaped, color-coded line to the log panel."""

        color = LOG_COLORS.get(level, LOG_COLORS["info"])
        label = escape(level.upper())
        safe_message = escape(message)
        self.log_view.append(
            f'<span style="color:{color}; font-weight:600">[{label}]</span> '
            f'<span style="color:#cdd6f4">{safe_message}</span>'
        )

    @Slot(str)
    def _show_code(self, content: str) -> None:
        """Store code preview content received from the worker."""

        self._latest_code = content
        self._refresh_code_panel()

    @Slot(str)
    def _show_diff(self, content: str) -> None:
        """Store a diff preview and enable the decision placeholders."""

        self._latest_diff = content
        self._refresh_code_panel()
        self.apply_button.setEnabled(True)
        self.reject_button.setEnabled(True)

    def _refresh_code_panel(self) -> None:
        """Render the latest code and diff in their shared preview panel."""

        sections: list[str] = []
        if self._latest_code:
            sections.append("# 候选代码\n" + self._latest_code.rstrip())
        if self._latest_diff:
            sections.append("# Unified Diff\n" + self._latest_diff.rstrip())
        self.code_view.setPlainText("\n\n".join(sections))

    @Slot(str, str)
    def _set_status(self, state: str, message: str) -> None:
        """Update the toolbar indicator and status-bar message."""

        normalized_state = state if state in STATUS_COLORS else "error"
        labels = {"ready": "就绪", "running": "运行中", "error": "错误"}
        color = STATUS_COLORS[normalized_state]
        self.status_indicator.setText(f"● {labels[normalized_state]}")
        self.status_indicator.setStyleSheet(f"color: {color}; font-weight: 600;")
        self.statusBar().showMessage(message)

    @Slot(bool, str)
    def _handle_worker_finished(self, success: bool, message: str) -> None:
        """Restore controls and display the final mock-run state."""

        self.send_button.setEnabled(True)
        self.run_action.setEnabled(True)
        self._set_status("ready" if success else "error", message)

    @Slot()
    def _open_workspace(self) -> None:
        """Ask the user for an existing workspace directory."""

        selected = QFileDialog.getExistingDirectory(
            self,
            "选择工作区",
            str(self.workspace_root or Path.cwd()),
        )
        if not selected:
            return
        try:
            self.workspace_root = Path(selected).resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as exc:
            self._set_status("error", f"工作区无效：{exc}")
            return
        self.snapshot_data = {}
        self._refresh_workspace_label()
        self._set_status("ready", "工作区已打开")

    def _refresh_workspace_label(self) -> None:
        """Show the active workspace path in the toolbar."""

        path_text = str(self.workspace_root) if self.workspace_root is not None else "未选择"
        self.workspace_label.setText(f"工作区：{path_text}")
        self.workspace_label.setToolTip(path_text)

    @Slot()
    def _save_snapshot(self) -> None:
        """Capture metadata through the existing Phase 2 snapshot hook."""

        if self.workspace_root is None:
            self._set_status("error", "请先打开工作区")
            return
        try:
            self.snapshot_data = save_workspace_snapshot(self.workspace_root)
        except (OSError, RuntimeError, ValueError) as exc:
            self._set_status("error", f"快照保存失败：{exc}")
            return
        self._set_status("ready", f"已记录 {len(self.snapshot_data)} 个文件的快照")

    @Slot()
    def _rollback_snapshot(self) -> None:
        """Call the Phase 2 rollback stub without claiming restoration succeeded."""

        if not self.snapshot_data:
            self._set_status("error", "尚未保存快照")
            return
        restored = rollback_to_snapshot(self.snapshot_data)
        if restored:
            self._set_status("ready", "已回退到快照")
        else:
            self._set_status("error", "回退功能尚未完整实现")

    def _set_mode(self, mode: str, checked: bool) -> None:
        """Update the displayed Agent mode when its checked action fires."""

        if not checked:
            return
        self.mode = mode
        self.mode_label.setText(f"模式：{mode}")
        self.statusBar().showMessage(f"已切换为 {mode} 模式")

    @Slot(bool)
    def _set_interactive_confirmation(self, checked: bool) -> None:
        """Record the future GUI write-confirmation preference."""

        self.interactive_confirmation = checked
        state = "开启" if checked else "关闭"
        self.statusBar().showMessage(f"交互确认已{state}")

    @Slot()
    def _apply_placeholder_change(self) -> None:
        """Acknowledge a mock diff; no file mutation occurs in Phase 4."""

        self._append_log("已确认模拟修改（Phase 4 不写入磁盘）。", "success")
        self.apply_button.setEnabled(False)
        self.reject_button.setEnabled(False)

    @Slot()
    def _reject_placeholder_change(self) -> None:
        """Reject the mock diff and clear the pending preview."""

        self._append_log("已拒绝模拟修改。", "warning")
        self._latest_diff = ""
        self._refresh_code_panel()
        self.apply_button.setEnabled(False)
        self.reject_button.setEnabled(False)

    def closeEvent(self, event: QCloseEvent) -> None:
        """Request an active placeholder worker to stop before window teardown."""

        if self.worker is not None and self.worker.isRunning():
            self.worker.requestInterruption()
            self.worker.wait(1_500)
        super().closeEvent(event)
