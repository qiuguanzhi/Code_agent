"""PySide6 main window for sessions, workspace files, tabs, and Agent runs."""

from __future__ import annotations

import os
import shutil
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QDir,
    QModelIndex,
    QPoint,
    QSettings,
    QTimer,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QFontDatabase,
    QKeySequence,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFileSystemModel,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTextBrowser,
    QTextEdit,
    QToolBar,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from gui.session import Conversation, ConversationStore
from gui.theme import get_theme
from gui.widgets import ConversationScrollArea
from gui.worker import AgentWorker
from tools.filesystem import (
    DEFAULT_MAX_WRITE_BYTES,
    resolve_in_workspace,
    sha256_file_streaming,
    write_file,
)
from utils.snapshot import (
    SNAPSHOT_META_KEY,
    discard_workspace_snapshot,
    rollback_to_snapshot,
    save_workspace_snapshot,
)


class MainWindow(QMainWindow):
    """Coordinate native GUI state while Agent work stays in a QThread."""

    confirm_signal = Signal(bool)

    def __init__(
        self,
        workspace_root: Path | None = None,
        *,
        settings: QSettings | None = None,
    ) -> None:
        """Create the three-column desktop interface and restore UI state."""

        super().__init__()
        self._launch_cwd = Path.cwd().resolve()
        self.workspace_root = self._normalize_workspace(workspace_root)
        self.settings = settings or QSettings("MiniCodingAgent", "Desktop")
        self.session_store = ConversationStore(self.settings)
        self.session_store.reset()
        saved_theme = str(self.settings.value("ui/theme", "dark"))
        self.theme_name = saved_theme if saved_theme in {"dark", "light"} else "dark"
        _, self.theme_colors = get_theme(self.theme_name)

        self.snapshot_data: dict[str, str] = {}
        self.snapshot_timestamp: str | None = None
        saved_thinking_mode = str(self.settings.value("ui/thinking_mode", "quick"))
        self.thinking_mode = (
            saved_thinking_mode
            if saved_thinking_mode in {"quick", "deep"}
            else "quick"
        )
        self.mode = "goal" if self.thinking_mode == "deep" else "auto"
        # Desktop writes are always staged for explicit user approval.  Manual
        # editor saves use their own button and intentionally do not enter this
        # Agent confirmation path.
        self.interactive_confirmation = True
        self.max_steps = 20
        self.worker: AgentWorker | None = None
        self._running_session_id: str | None = None
        self._close_pending = False
        self._awaiting_confirmation = False
        self._pending_write_path = ""
        self._workspace_collapsed = False
        self._workspace_saved_width = 200
        self.workspace_files: set[str] = set()
        self._tab_editors: dict[str, QTextEdit] = {}
        self._tab_previews: dict[str, dict[str, Any]] = {}
        self._rendering_editor = False
        self._waiting_blink_on = False
        self._loading_tick = 0
        self._tool_status_state = "idle"
        self._tool_status_name = ""
        self._tool_error_detail = ""

        self.setWindowTitle("Mini Coding Agent")
        self.setAcceptDrops(True)
        self.resize(1280, 780)
        self.setMinimumSize(940, 580)

        self._create_actions()
        self._create_menu_bar()
        self._create_tool_bar()
        self._create_central_widget()
        self.statusBar().setObjectName("statusBar")

        self.waiting_timer = QTimer(self)
        self.waiting_timer.setInterval(500)
        self.waiting_timer.timeout.connect(self._blink_waiting_indicator)
        self.loading_timer = QTimer(self)
        self.loading_timer.setInterval(400)
        self.loading_timer.timeout.connect(self._animate_loading_text)

        self._refresh_session_combo()
        self._render_active_session()
        self._populate_workspace_files()
        self._refresh_workspace_label()
        self._refresh_snapshot_label()
        self._apply_theme()
        if self.workspace_root is not None:
            self._synchronize_process_cwd(self.workspace_root)
        self.update_status("ready", "就绪")

    @staticmethod
    def _normalize_workspace(workspace_root: Path | None) -> Path | None:
        """Resolve an optional workspace and reject non-directory values."""

        if workspace_root is None:
            return None
        try:
            resolved = workspace_root.resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValueError("workspace cannot be resolved") from exc
        if not resolved.is_dir():
            raise ValueError("workspace must be a directory")
        return resolved

    @staticmethod
    def _fixed_width_font() -> QFont:
        """Return a valid fixed-width font without Qt's negative-size warning."""

        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        if font.pointSizeF() <= 0:
            font.setPointSize(10)
        return font

    def _create_actions(self) -> None:
        """Create menu and toolbar actions."""

        self.open_workspace_action = QAction("打开工作区", self)
        self.open_workspace_action.setObjectName("openWorkspaceAction")
        self.open_workspace_action.setShortcut(QKeySequence("Ctrl+O"))
        self.open_workspace_action.triggered.connect(self._open_workspace)

        self.save_snapshot_action = QAction("保存快照", self)
        self.save_snapshot_action.setObjectName("saveSnapshotAction")
        self.save_snapshot_action.setShortcut(QKeySequence("Ctrl+S"))
        self.save_snapshot_action.triggered.connect(self._save_snapshot)

        self.rollback_action = QAction("退回初始快照", self)
        self.rollback_action.setObjectName("rollbackAction")
        self.rollback_action.setShortcut(QKeySequence("Ctrl+R"))
        self.rollback_action.triggered.connect(self._rollback_snapshot)

        self.interactive_action = QAction("Agent 文件修改必须批准", self, checkable=True)
        self.interactive_action.setObjectName("interactiveAction")
        self.interactive_action.setChecked(True)
        self.interactive_action.setEnabled(False)
        self.interactive_action.setToolTip("GUI 模式下，Agent 的 write_file 始终先显示 Diff")
        self.interactive_action.toggled.connect(self._set_interactive_confirmation)

        self.rollback_toolbar_action = QAction("↩ 回退", self)
        self.rollback_toolbar_action.triggered.connect(self._rollback_snapshot)

    def _create_menu_bar(self) -> None:
        """Populate File, Settings, and View menus."""

        file_menu = self.menuBar().addMenu("文件")
        file_menu.addAction(self.open_workspace_action)
        file_menu.addAction(self.save_snapshot_action)
        file_menu.addAction(self.rollback_action)

        settings_menu = self.menuBar().addMenu("设置")
        settings_menu.addAction(self.interactive_action)

        view_menu = self.menuBar().addMenu("视图")
        workspace_action = QAction("折叠/展开工作区", self)
        workspace_action.triggered.connect(self._toggle_workspace_panel)
        view_menu.addAction(workspace_action)
        theme_action = QAction("切换亮色/暗色", self)
        theme_action.triggered.connect(self._toggle_theme)
        view_menu.addAction(theme_action)

    def _create_tool_bar(self) -> None:
        """Create workspace, snapshot, status, and theme controls."""

        toolbar = QToolBar("主工具栏", self)
        toolbar.setObjectName("mainToolbar")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self.status_indicator = QLabel()
        self.status_indicator.setObjectName("statusIndicator")
        toolbar.addWidget(self.status_indicator)
        toolbar.addSeparator()
        self.workspace_label = QLabel()
        self.workspace_label.setObjectName("workspaceLabel")
        toolbar.addWidget(self.workspace_label)
        toolbar.addSeparator()

        self.select_workspace_button = QPushButton("📁 选择工作区", self)
        self.select_workspace_button.setObjectName("selectWorkspaceButton")
        self.select_workspace_button.clicked.connect(self._open_workspace)
        toolbar.addWidget(self.select_workspace_button)
        toolbar.addSeparator()

        self.save_snapshot_button = QPushButton("💾 保存快照", self)
        self.save_snapshot_button.setObjectName("saveSnapshotButton")
        self.save_snapshot_button.clicked.connect(self._save_snapshot)
        toolbar.addWidget(self.save_snapshot_button)
        self.snapshot_label = QLabel("快照时间：未保存", self)
        self.snapshot_label.setObjectName("snapshotLabel")
        toolbar.addWidget(self.snapshot_label)
        toolbar.addAction(self.rollback_toolbar_action)
        toolbar.addSeparator()

        self.workspace_toggle_button = QPushButton("◀ 工作区", self)
        self.workspace_toggle_button.clicked.connect(self._toggle_workspace_panel)
        toolbar.addWidget(self.workspace_toggle_button)
        self.theme_button = QPushButton(self)
        self.theme_button.setObjectName("themeButton")
        self.theme_button.clicked.connect(self._toggle_theme)
        toolbar.addWidget(self.theme_button)

    def _create_central_widget(self) -> None:
        """Build the adjustable workspace, conversation, and code columns."""

        root = QWidget(self)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(8)

        self.splitter = QSplitter(Qt.Orientation.Horizontal, root)
        self.splitter.setObjectName("mainSplitter")
        self.splitter.splitterMoved.connect(self._remember_workspace_width)
        self.workspace_panel = self._create_workspace_panel()
        conversation_panel = self._create_conversation_panel()
        code_panel = self._create_code_panel()
        self.splitter.addWidget(self.workspace_panel)
        self.splitter.addWidget(conversation_panel)
        self.splitter.addWidget(code_panel)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 3)
        self.splitter.setStretchFactor(2, 2)
        self.splitter.setSizes([200, 620, 460])
        root_layout.addWidget(self.splitter, 1)
        self.tool_status_button = QPushButton("🔧 暂无工具调用", root)
        self.tool_status_button.setObjectName("toolStatusButton")
        self.tool_status_button.setFixedHeight(30)
        self.tool_status_button.setEnabled(False)
        self.tool_status_button.clicked.connect(self._show_tool_error_detail)
        root_layout.addWidget(self.tool_status_button)
        self.setCentralWidget(root)

    def _create_workspace_panel(self) -> QWidget:
        """Create a resizable, editable filesystem tree."""

        panel = QWidget(self)
        panel.setObjectName("workspacePanel")
        panel.setMinimumWidth(120)
        panel.setMaximumWidth(400)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 4, 4, 4)
        header = QHBoxLayout()
        header.addWidget(QLabel("工作区文件", panel))
        header.addStretch(1)
        self.workspace_collapse_button = QPushButton("◀", panel)
        self.workspace_collapse_button.setFixedWidth(32)
        self.workspace_collapse_button.clicked.connect(self._toggle_workspace_panel)
        header.addWidget(self.workspace_collapse_button)
        layout.addLayout(header)
        self.workspace_model = QFileSystemModel(self)
        self.workspace_model.setReadOnly(True)
        self.workspace_model.setFilter(
            QDir.Filter.AllDirs
            | QDir.Filter.Files
            | QDir.Filter.NoDotAndDotDot
        )
        self.workspace_tree = QTreeView(panel)
        self.workspace_tree.setObjectName("workspaceFileTree")
        self.workspace_tree.setModel(self.workspace_model)
        self.workspace_tree.setHeaderHidden(True)
        self.workspace_tree.setAnimated(True)
        self.workspace_tree.setExpandsOnDoubleClick(False)
        for column in range(1, 4):
            self.workspace_tree.hideColumn(column)
        self.workspace_tree.doubleClicked.connect(self._activate_workspace_index)
        self.workspace_tree.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.workspace_tree.customContextMenuRequested.connect(
            self._show_workspace_context_menu
        )
        layout.addWidget(self.workspace_tree, 1)
        self.workspace_empty_label = QLabel("尚未选择工作区", panel)
        self.workspace_empty_label.setObjectName("workspaceEmptyLabel")
        self.workspace_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.workspace_empty_label, 1)
        return panel

    def _create_conversation_panel(self) -> QWidget:
        """Create session history, message stream, compact logs, and input."""

        panel = QWidget(self)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        session_row = QHBoxLayout()
        session_row.addWidget(QLabel("会话", panel))
        self.session_combo = QComboBox(panel)
        self.session_combo.setObjectName("sessionCombo")
        self.session_combo.currentIndexChanged.connect(self._switch_session)
        session_row.addWidget(self.session_combo, 1)
        self.new_session_button = QPushButton("＋ 新建", panel)
        self.new_session_button.clicked.connect(self._new_session)
        session_row.addWidget(self.new_session_button)
        self.delete_session_button = QPushButton("删除会话", panel)
        self.delete_session_button.setObjectName("deleteSessionButton")
        self.delete_session_button.clicked.connect(self._delete_active_session)
        session_row.addWidget(self.delete_session_button)
        layout.addLayout(session_row)

        vertical_splitter = QSplitter(Qt.Orientation.Vertical, panel)
        self.conversation_view = ConversationScrollArea(vertical_splitter)
        self.conversation_view.delete_requested.connect(
            self._delete_conversation_message
        )

        log_panel = QWidget(vertical_splitter)
        log_panel.setObjectName("logPanel")
        log_layout = QVBoxLayout(log_panel)
        log_layout.setContentsMargins(8, 8, 8, 8)
        log_layout.setSpacing(6)
        self.thinking_container = QWidget(log_panel)
        self.thinking_container.setObjectName("thinkingContainer")
        thinking_layout = QVBoxLayout(self.thinking_container)
        thinking_layout.setContentsMargins(10, 8, 10, 8)
        self.thinking_toggle = QPushButton("▶ 💭 深度思考", log_panel)
        self.thinking_toggle.setObjectName("thinkingToggle")
        self.thinking_toggle.setCheckable(True)
        self.thinking_toggle.setChecked(False)
        self.thinking_toggle.toggled.connect(self._toggle_thinking_details)
        thinking_layout.addWidget(self.thinking_toggle)
        self.thinking_view = QTextBrowser(log_panel)
        self.thinking_view.setObjectName("thinkingView")
        self.thinking_view.setMaximumHeight(180)
        self.thinking_view.setVisible(False)
        thinking_layout.addWidget(self.thinking_view)
        self.thinking_container.setVisible(False)
        log_layout.addWidget(self.thinking_container)

        self.log_view = QTextEdit(log_panel)
        self.log_view.setObjectName("logView")
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("事件记录")
        self.log_view.setFont(self._fixed_width_font())
        log_layout.addWidget(self.log_view, 1)
        vertical_splitter.addWidget(self.conversation_view)
        vertical_splitter.addWidget(log_panel)
        vertical_splitter.setSizes([360, 260])
        layout.addWidget(vertical_splitter, 1)

        self.loading_container = QWidget(panel)
        loading_layout = QHBoxLayout(self.loading_container)
        loading_layout.setContentsMargins(0, 0, 0, 0)
        self.loading_bar = QProgressBar(panel)
        self.loading_bar.setObjectName("thinkingProgress")
        self.loading_bar.setRange(0, 0)
        self.loading_bar.setTextVisible(False)
        self.loading_bar.setFixedWidth(96)
        loading_layout.addWidget(self.loading_bar)
        self.loading_label = QLabel("思考中", panel)
        self.loading_label.setObjectName("loadingLabel")
        loading_layout.addWidget(self.loading_label)
        loading_layout.addStretch(1)
        self.loading_container.setVisible(False)
        layout.addWidget(self.loading_container)

        self.waiting_indicator = QLabel("✋ 需要您确认才能继续", panel)
        self.waiting_indicator.setObjectName("waitingIndicator")
        self.waiting_indicator.setVisible(False)
        layout.addWidget(self.waiting_indicator)
        input_row = QHBoxLayout()
        self.task_input = QLineEdit(panel)
        self.task_input.setObjectName("taskInput")
        self.task_input.setPlaceholderText("")
        self.task_input.returnPressed.connect(self._submit_task)
        input_row.addWidget(self.task_input, 1)
        self.send_button = QPushButton("发送", panel)
        self.send_button.setObjectName("sendButton")
        self.send_button.clicked.connect(self._submit_task)
        input_row.addWidget(self.send_button)
        self.thinking_mode_combo = QComboBox(panel)
        self.thinking_mode_combo.setObjectName("thinkingModeCombo")
        self.thinking_mode_combo.addItem("快速", "quick")
        self.thinking_mode_combo.addItem("深度思考", "deep")
        selected_index = self.thinking_mode_combo.findData(self.thinking_mode)
        self.thinking_mode_combo.setCurrentIndex(max(0, selected_index))
        self.thinking_mode_combo.currentIndexChanged.connect(
            self._set_thinking_mode
        )
        input_row.addWidget(self.thinking_mode_combo)
        layout.addLayout(input_row)
        return panel

    def _create_code_panel(self) -> QWidget:
        """Create empty state, editable file tabs, save, and Diff decisions."""

        panel = QWidget(self)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        self.code_stack = QStackedWidget(panel)
        self.code_stack.setObjectName("codeStack")
        self.code_empty_page = QWidget(self.code_stack)
        self.code_empty_page.setObjectName("codeEmptyPage")
        empty_layout = QVBoxLayout(self.code_empty_page)
        empty_layout.setContentsMargins(0, 0, 0, 0)
        empty_label = QLabel("未打开文件", self.code_empty_page)
        empty_label.setObjectName("codeEmptyLabel")
        empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_label, 1)
        self.code_stack.addWidget(self.code_empty_page)

        self.code_tabs = QTabWidget(self.code_stack)
        self.code_tabs.setObjectName("codeTabs")
        self.code_tabs.setTabsClosable(True)
        self.code_tabs.setTabBarAutoHide(False)
        self.code_tabs.tabCloseRequested.connect(self._close_code_tab)
        self.code_tabs.currentChanged.connect(self._code_tab_changed)
        self.code_stack.addWidget(self.code_tabs)
        self.code_stack.setCurrentWidget(self.code_empty_page)
        layout.addWidget(self.code_stack, 1)

        self._empty_code_view = QTextEdit(panel)
        self._empty_code_view.setReadOnly(True)
        # Compatibility target for code_view while the stack shows its real
        # empty page.  An unparented layout child defaults to a visible 100x30
        # editor, which was the stray rounded rectangle reported by users.
        self._empty_code_view.hide()
        self.code_view = self._empty_code_view
        self._sync_code_stack()

        manual_row = QHBoxLayout()
        self.manual_file_status = QLabel("选择工作区文件后可手动编辑", panel)
        self.manual_file_status.setObjectName("manualFileStatus")
        manual_row.addWidget(self.manual_file_status)
        manual_row.addStretch(1)
        self.manual_save_button = QPushButton("保存文件", panel)
        self.manual_save_button.setObjectName("manualSaveButton")
        self.manual_save_button.setEnabled(False)
        self.manual_save_button.clicked.connect(self._save_manual_file)
        manual_row.addWidget(self.manual_save_button)
        layout.addLayout(manual_row)

        self.decision_widget = QWidget(panel)
        decisions = QHBoxLayout(self.decision_widget)
        decisions.setContentsMargins(0, 0, 0, 0)
        decisions.addStretch(1)
        self.apply_button = QPushButton("应用修改", panel)
        self.apply_button.setObjectName("applyButton")
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self._apply_pending_change)
        decisions.addWidget(self.apply_button)
        self.reject_button = QPushButton("拒绝", panel)
        self.reject_button.setObjectName("rejectButton")
        self.reject_button.setEnabled(False)
        self.reject_button.clicked.connect(self._reject_pending_change)
        decisions.addWidget(self.reject_button)
        self.decision_widget.setVisible(False)
        layout.addWidget(self.decision_widget)
        return panel

    def _create_worker(self, task: str) -> AgentWorker:
        """Create the production worker at a test-injection boundary."""

        _ = task
        return AgentWorker(mode=self.mode)

    @Slot()
    def _submit_task(self) -> None:
        """Capture, clear, persist, and submit one user message."""

        task = self.task_input.text().strip()
        if not task:
            return
        self.task_input.clear()
        if self.worker is not None and self.worker.isRunning():
            self.update_status("error", "已有任务正在运行")
            return
        conversation = self.session_store.active
        self.session_store.add_message("user", task, conversation_id=conversation.id)
        self._refresh_session_combo()
        self._render_active_session()

        if self.workspace_root is None:
            self.session_store.add_message(
                "system",
                "请先打开工作区。",
                conversation_id=conversation.id,
            )
            self._render_active_session()
            self.update_status("error", "请先打开工作区")
            return
        self._running_session_id = conversation.id
        self.apply_button.setEnabled(False)
        self.reject_button.setEnabled(False)
        self.decision_widget.setVisible(False)
        self.send_button.setEnabled(False)
        self.thinking_mode_combo.setEnabled(False)
        self._start_loading()
        if self.thinking_mode == "deep":
            self.thinking_container.setVisible(True)
        self.update_status("running", "Agent 正在运行")

        self.worker = self._create_worker(task)
        self.worker.log_signal.connect(self.update_log)
        self.worker.code_signal.connect(self.update_code)
        self.worker.diff_signal.connect(self.update_diff)
        self.worker.status_signal.connect(self.update_status)
        self.worker.confirmation_signal.connect(self._mark_confirmation_pending)
        self.worker.snapshot_signal.connect(self._store_agent_snapshot)
        self.worker.finished_signal.connect(self._handle_worker_finished)
        self.worker.progress_signal.connect(self._append_process_update)
        self.worker.tool_status_signal.connect(self.update_tool_status)
        self.confirm_signal.connect(self.worker.resolve_write_confirmation)
        try:
            self.worker.start_agent(
                task,
                self.workspace_root,
                self.max_steps,
                self.interactive_confirmation,
            )
        except (RuntimeError, ValueError) as exc:
            self._disconnect_confirmation()
            self.send_button.setEnabled(True)
            self.thinking_mode_combo.setEnabled(True)
            self._stop_loading()
            self._running_session_id = None
            self.update_status("error", str(exc))

    @Slot(int, str, str, str, str)
    def update_log(self, step: int, icon: str, label: str, color: str, message: str) -> None:
        """Persist and display one compact tool or system log line."""

        conversation_id = self._running_session_id or self.session_store.active_id
        record = {
            "step": step,
            "icon": icon,
            "label": label,
            "color": color,
            "message": message,
        }
        self.session_store.add_log(record, conversation_id=conversation_id)
        if conversation_id == self.session_store.active_id and step == 0:
            self.log_view.append(self._format_log_html(record))

    @Slot(str, str, str)
    def update_tool_status(self, state: str, tool_name: str, detail: str) -> None:
        """Show only the current/latest tool in a fixed one-line status control."""

        normalized = state if state in {"running", "success", "error"} else "running"
        icons = {"running": "🔧", "success": "✅", "error": "❌"}
        self._tool_status_state = normalized
        self._tool_status_name = tool_name
        self._tool_error_detail = detail if normalized == "error" else ""
        suffix = "  ▸" if normalized == "error" else ""
        self.tool_status_button.setText(f"{icons[normalized]} {tool_name}{suffix}")
        self.tool_status_button.setEnabled(normalized == "error")
        self._refresh_tool_status_style()

    @Slot()
    def _show_tool_error_detail(self) -> None:
        """Open the full latest tool failure only when one is available."""

        if self._tool_status_state != "error" or not self._tool_error_detail:
            return
        QMessageBox.critical(
            self,
            f"{self._tool_status_name} 执行失败",
            self._tool_error_detail,
        )

    def _refresh_tool_status_style(self) -> None:
        """Apply a semantic one-line color without growing the status bar."""

        color_keys = {
            "idle": "muted",
            "running": "accent",
            "success": "success",
            "error": "error",
        }
        color = self.theme_colors[color_keys.get(self._tool_status_state, "muted")]
        self.tool_status_button.setStyleSheet(
            f"text-align:left; color:{color}; font-weight:600;"
        )

    def _format_log_html(self, record: dict[str, Any]) -> str:
        """Format a compact record as exactly one visible HTML line."""

        step = record.get("step", 0)
        icon = escape(str(record.get("icon", "•")))
        label = escape(str(record.get("label", "")))
        semantic = str(record.get("color", "accent"))
        color = self.theme_colors.get(semantic, semantic)
        if not color.startswith("#"):
            color = self.theme_colors["accent"]
        message = str(record.get("message", "")).strip()
        suffix = f" - {escape(message)}" if message else ""
        return (
            f'<span style="color:{self.theme_colors["muted"]}">[{step}]</span> '
            f'<span style="color:{color}; font-weight:600">{icon} {label}{suffix}</span>'
        )

    @Slot(int, str)
    def _append_process_update(self, level: int, summary: str) -> None:
        """Persist and render one auditable high-level work-process update."""

        session_id = self._running_session_id or self.session_store.active_id
        self.session_store.add_process(level, summary, conversation_id=session_id)
        if session_id == self.session_store.active_id:
            self._render_process(self.session_store.active)

    def _render_process(self, conversation: Conversation) -> None:
        """Render safe process summaries in the collapsible deep-mode panel."""

        rows: list[str] = []
        for index, item in enumerate(conversation.process, start=1):
            level = item.get("level", 0)
            text = item.get("text", "")
            safe_level = level if isinstance(level, int) else 0
            safe_text = escape(str(text))
            marker = "▸" if safe_level == 0 else "·"
            prefix = f"{index}." if safe_level == 0 else marker
            rows.append(
                f'<div style="margin-left:{safe_level * 20}px; margin-bottom:6px; '
                f'color:{self.theme_colors["muted"]}">{prefix} {safe_text}</div>'
            )
        self.thinking_view.setHtml("".join(rows))
        should_show = self.thinking_mode == "deep" and (
            bool(conversation.process)
            or self.worker is not None
            and self.worker.isRunning()
        )
        self.thinking_container.setVisible(should_show)
        count = len(conversation.process)
        running = self.worker is not None and self.worker.isRunning()
        summary = "思考中..." if running else f"深度思考（{count} 步）"
        marker = "▼" if self.thinking_toggle.isChecked() else "▶"
        self.thinking_toggle.setText(f"{marker} 💭 {summary}")
        self.thinking_view.setVisible(
            should_show and self.thinking_toggle.isChecked()
        )
        if should_show:
            scrollbar = self.thinking_view.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    @Slot(bool)
    def _toggle_thinking_details(self, expanded: bool) -> None:
        """Expand or collapse the high-level process summary."""

        self.thinking_view.setVisible(expanded)
        marker = "▼" if expanded else "▶"
        conversation = self.session_store.active
        running = self.worker is not None and self.worker.isRunning()
        summary = (
            "思考中..."
            if running
            else f"深度思考（{len(conversation.process)} 步）"
        )
        self.thinking_toggle.setText(f"{marker} 💭 {summary}")

    def _start_loading(self) -> None:
        """Show an immediate native indeterminate progress animation."""

        self._loading_tick = 0
        self.loading_label.setText("思考中")
        self.loading_container.setVisible(True)
        self.loading_timer.start()

    def _stop_loading(self) -> None:
        """Stop and hide the request loading feedback."""

        self.loading_timer.stop()
        self.loading_container.setVisible(False)
        self._loading_tick = 0

    @Slot()
    def _animate_loading_text(self) -> None:
        """Animate the thinking label while the native bar is indeterminate."""

        self._loading_tick = (self._loading_tick + 1) % 4
        self.loading_label.setText("思考中" + "." * self._loading_tick)

    @Slot(str, str)
    def update_code(self, file_path: str, content: str) -> None:
        """Open or update a file tab from Agent read_file or drag preview."""

        workspace_backed, base_sha256 = self._workspace_backing(file_path)
        state = self._tab_previews.setdefault(
            file_path,
            {
                "code": "",
                "diff": "",
                "additions": 0,
                "deletions": 0,
                "pending": False,
                "workspace_backed": False,
                "base_sha256": "",
                "dirty": False,
            },
        )
        state.update(
            {
                "code": content,
                "diff": "",
                "additions": 0,
                "deletions": 0,
                "pending": False,
                "workspace_backed": workspace_backed,
                "base_sha256": base_sha256,
                "dirty": False,
            }
        )
        self._ensure_code_tab(file_path)
        self._render_code_tab(file_path)

    def _workspace_backing(self, file_path: str) -> tuple[bool, str]:
        """Return whether a preview maps to a current regular workspace file."""

        if self.workspace_root is None:
            return False, ""
        try:
            target = resolve_in_workspace(
                self.workspace_root,
                file_path,
                must_exist=True,
            )
            if not target.is_file():
                return False, ""
            return True, sha256_file_streaming(target)
        except (OSError, RuntimeError, ValueError):
            return False, ""

    @Slot(str, str, int, int)
    def update_diff(
        self,
        file_path: str,
        diff_text: str,
        additions: int,
        deletions: int,
    ) -> None:
        """Open a colorized Diff in its file tab."""

        workspace_backed, base_sha256 = self._workspace_backing(file_path)
        original_content = ""
        if workspace_backed and self.workspace_root is not None:
            try:
                target = resolve_in_workspace(
                    self.workspace_root,
                    file_path,
                    must_exist=True,
                )
                original_content = target.read_text(encoding="utf-8")
            except (OSError, RuntimeError, UnicodeError, ValueError):
                workspace_backed = False
                base_sha256 = ""

        state = self._tab_previews.setdefault(
            file_path,
            {
                "code": "",
                "diff": "",
                "additions": 0,
                "deletions": 0,
                "pending": False,
                "workspace_backed": False,
                "base_sha256": "",
                "dirty": False,
            },
        )
        state.update(
            {
                "code": original_content,
                "diff": diff_text,
                "additions": additions,
                "deletions": deletions,
                "pending": True,
                "workspace_backed": workspace_backed,
                "base_sha256": base_sha256,
                "dirty": False,
            }
        )
        self._ensure_code_tab(file_path)
        self._render_code_tab(file_path)

    def _ensure_code_tab(self, file_path: str) -> QTextEdit:
        """Create or activate one independent closable file editor."""

        editor = self._tab_editors.get(file_path)
        if editor is None:
            editor = QTextEdit(self.code_tabs)
            editor.setObjectName("codeView")
            editor.setAcceptRichText(False)
            editor.setFont(self._fixed_width_font())
            editor.setPlaceholderText("无代码预览")
            editor.textChanged.connect(
                lambda path=file_path: self._on_editor_text_changed(path)
            )
            self._tab_editors[file_path] = editor
            index = self.code_tabs.addTab(editor, Path(file_path).name or file_path)
            self.code_tabs.setTabToolTip(index, file_path)
            self._sync_code_stack()
        self.code_tabs.setCurrentWidget(editor)
        self.code_view = editor
        return editor

    def _render_code_tab(self, file_path: str) -> None:
        """Render an editable plain file or a read-only colorized Diff."""

        editor = self._tab_editors.get(file_path)
        state = self._tab_previews.get(file_path)
        if editor is None or state is None:
            return
        diff_text = str(state.get("diff", ""))
        self._rendering_editor = True
        try:
            if diff_text:
                editor.setReadOnly(True)
                additions = int(state.get("additions", 0))
                deletions = int(state.get("deletions", 0))
                colored_lines: list[str] = []
                for line in diff_text.rstrip().splitlines():
                    if line.startswith("+") and not line.startswith("+++"):
                        color = self.theme_colors["success"]
                    elif line.startswith("-") and not line.startswith("---"):
                        color = self.theme_colors["error"]
                    elif line.startswith("@@"):
                        color = self.theme_colors["purple"]
                    else:
                        color = self.theme_colors["muted"]
                    colored_lines.append(
                        f'<span style="color:{color}">{escape(line)}</span>'
                    )
                header = f"Unified Diff：{file_path} (+{additions} / -{deletions})"
                editor.setHtml(
                    f'<div style="color:{self.theme_colors["warning"]}; '
                    f'font-weight:700; margin-bottom:8px">{escape(header)}</div>'
                    f'<pre style="background:{self.theme_colors["code_background"]}">'
                    + "\n".join(colored_lines)
                    + "</pre>"
                )
            else:
                editor.setReadOnly(not bool(state.get("workspace_backed")))
                editor.setPlainText(str(state.get("code", "")))
                editor.document().setModified(bool(state.get("dirty")))
        finally:
            self._rendering_editor = False
        self._refresh_manual_save_control()

    @Slot()
    def _save_manual_file(self) -> None:
        """Persist the active user's plain-text edits without Agent Diff approval."""

        file_path = self._current_code_path()
        if file_path is None or self.workspace_root is None:
            return
        state = self._tab_previews.get(file_path)
        editor = self._tab_editors.get(file_path)
        if state is None or editor is None:
            return
        if state.get("pending") is True or state.get("diff"):
            self.update_status("error", "请先处理 Agent 提出的 Diff")
            return
        if state.get("workspace_backed") is not True:
            self.update_status("error", "仅预览文件不能直接保存到工作区")
            return
        result = write_file(
            self.workspace_root,
            file_path,
            editor.toPlainText(),
            str(state.get("base_sha256", "")),
        )
        if result.get("ok") is not True:
            error = result.get("error")
            reason = (
                str(error.get("message", "保存失败"))
                if isinstance(error, dict)
                else "保存失败"
            )
            self.update_status("error", reason)
            return
        meta = result.get("meta")
        new_hash = str(meta.get("sha256", "")) if isinstance(meta, dict) else ""
        state.update(
            {
                "code": editor.toPlainText(),
                "base_sha256": new_hash,
                "dirty": False,
            }
        )
        editor.document().setModified(False)
        self._set_tab_dirty(file_path, False)
        self._refresh_manual_save_control()
        self.update_status("ready", f"已手动保存：{file_path}")

    def _on_editor_text_changed(self, file_path: str) -> None:
        """Track manual edits without mixing them into Agent Diff state."""

        if self._rendering_editor:
            return
        state = self._tab_previews.get(file_path)
        editor = self._tab_editors.get(file_path)
        if state is None or editor is None or state.get("diff"):
            return
        state["code"] = editor.toPlainText()
        if state.get("workspace_backed") is True:
            state["dirty"] = True
            self._set_tab_dirty(file_path, True)
        self._refresh_manual_save_control()

    def _current_code_path(self) -> str | None:
        """Return the path represented by the current code tab."""

        widget = self.code_tabs.currentWidget()
        return next(
            (path for path, editor in self._tab_editors.items() if editor is widget),
            None,
        )

    def _set_tab_dirty(self, file_path: str, dirty: bool) -> None:
        """Reflect unsaved manual state with a conventional tab asterisk."""

        editor = self._tab_editors.get(file_path)
        if editor is None:
            return
        index = self.code_tabs.indexOf(editor)
        if index < 0:
            return
        base_name = Path(file_path).name or file_path
        self.code_tabs.setTabText(index, f"{base_name}*" if dirty else base_name)

    def _refresh_manual_save_control(self) -> None:
        """Enable manual save only for a dirty, editable workspace file."""

        file_path = self._current_code_path()
        state = self._tab_previews.get(file_path) if file_path is not None else None
        enabled = bool(
            state is not None
            and state.get("workspace_backed") is True
            and state.get("dirty") is True
            and not state.get("diff")
            and state.get("pending") is not True
        )
        self.manual_save_button.setEnabled(enabled)
        if state is None:
            text = "选择工作区文件后可手动编辑"
        elif state.get("diff"):
            text = "Agent 修改提案：请批准或拒绝"
        elif state.get("workspace_backed") is not True:
            text = "仅预览（不可保存）"
        elif state.get("dirty") is True:
            text = "有未保存的手动修改"
        else:
            text = "可手动编辑"
        self.manual_file_status.setText(text)

    def _sync_code_stack(self) -> None:
        """Show a seamless empty page or the real tab widget, never a fake tab."""

        if self.code_tabs.count() == 0:
            # Removing the unused QTabWidget also prevents Qt from leaving its
            # default 100x30 pane painted over the empty page on some Windows
            # styles/platform plugins.
            if self.code_stack.indexOf(self.code_tabs) >= 0:
                self.code_stack.removeWidget(self.code_tabs)
            self.code_tabs.hide()
            self.code_stack.setCurrentWidget(self.code_empty_page)
            self.code_view = self._empty_code_view
        else:
            if self.code_stack.indexOf(self.code_tabs) < 0:
                self.code_stack.addWidget(self.code_tabs)
            self.code_tabs.show()
            self.code_stack.setCurrentWidget(self.code_tabs)

    def _clear_diff_decoration(self, file_path: str, *, clear_code: bool) -> None:
        """Remove every Diff format immediately after a user decision."""

        state = self._tab_previews.get(file_path)
        if state is None:
            return
        state.update({"diff": "", "additions": 0, "deletions": 0})
        state["pending"] = False
        if clear_code:
            state["code"] = ""
        self._render_code_tab(file_path)

    @Slot(int)
    def _code_tab_changed(self, index: int) -> None:
        """Keep the compatibility code_view attribute on the active editor."""

        widget = self.code_tabs.widget(index) if index >= 0 else None
        self.code_view = widget if isinstance(widget, QTextEdit) else self._empty_code_view
        self._refresh_manual_save_control()

    @Slot(int)
    def _close_code_tab(self, index: int) -> None:
        """Close one tab and activate its nearest remaining neighbor."""

        widget = self.code_tabs.widget(index)
        key = next(
            (path for path, editor in self._tab_editors.items() if editor is widget),
            None,
        )
        if key is not None and self._tab_has_pending_diff(key):
            if not self._confirm_discard_pending("tab"):
                return
            if self._awaiting_confirmation and self._pending_write_path == key:
                self._reject_pending_change()
            else:
                self._clear_diff_decoration(key, clear_code=False)
        if key is not None:
            state = self._tab_previews.get(key)
            if state is not None and state.get("dirty") is True:
                decision = QMessageBox.question(
                    self,
                    "未保存的手动修改",
                    f"{key} 有未保存的手动修改，是否放弃并关闭？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if decision != QMessageBox.StandardButton.Yes:
                    return
        self.code_tabs.removeTab(index)
        if key is not None:
            editor = self._tab_editors.pop(key)
            self._tab_previews.pop(key, None)
            editor.deleteLater()
        self._sync_code_stack()
        self._code_tab_changed(self.code_tabs.currentIndex())

    def _tab_has_pending_diff(self, file_path: str) -> bool:
        """Return whether one tab contains a staged, undecided modification."""

        state = self._tab_previews.get(file_path)
        return bool(
            state is not None
            and state.get("pending") is True
            and str(state.get("diff", ""))
        )

    def _has_pending_diffs(self) -> bool:
        """Return whether any open tab still owns an undecided Diff."""

        return any(self._tab_has_pending_diff(path) for path in self._tab_previews)

    def _confirm_discard_pending(self, scope: str) -> bool:
        """Ask whether staged in-memory changes may be discarded."""

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        if scope == "window":
            box.setWindowTitle("退出确认")
            box.setText("仍有未应用的修改，是否放弃并退出？")
            discard_text = "放弃并退出"
        elif scope == "workspace":
            box.setWindowTitle("切换工作区")
            box.setText("仍有未应用的修改，是否放弃并切换工作区？")
            discard_text = "放弃并切换"
        else:
            box.setWindowTitle("关闭标签")
            box.setText("有未应用的修改，是否放弃？")
            discard_text = "放弃修改"
        discard_button = box.addButton(
            discard_text,
            QMessageBox.ButtonRole.DestructiveRole,
        )
        cancel_button = box.addButton(
            "取消",
            QMessageBox.ButtonRole.RejectRole,
        )
        box.setDefaultButton(cancel_button)
        box.exec()
        return box.clickedButton() is discard_button

    @Slot(str, str)
    def update_status(self, state: str, message: str) -> None:
        """Update the three-color toolbar indicator and status message."""

        normalized = state if state in {"ready", "running", "error"} else "error"
        labels = {"ready": "就绪", "running": "运行中", "error": "错误"}
        color_keys = {"ready": "success", "running": "warning", "error": "error"}
        color = self.theme_colors[color_keys[normalized]]
        self.status_indicator.setText(f"● {labels[normalized]}")
        self.status_indicator.setStyleSheet(f"color:{color}; font-weight:600")
        self.statusBar().showMessage(message)

    @Slot(bool, str)
    def _handle_worker_finished(self, success: bool, message: str) -> None:
        """Store the Agent reply and restore controls without adding verbose logs."""

        session_id = self._running_session_id or self.session_store.active_id
        self.session_store.add_message("assistant", message, conversation_id=session_id)
        if session_id == self.session_store.active_id:
            self._render_active_session()
        self.send_button.setEnabled(True)
        self.thinking_mode_combo.setEnabled(True)
        self._awaiting_confirmation = False
        self._pending_write_path = ""
        self.apply_button.setEnabled(False)
        self.reject_button.setEnabled(False)
        self.decision_widget.setVisible(False)
        self._stop_loading()
        self._stop_waiting_indicator()
        self._disconnect_confirmation()
        first_line = message.splitlines()[0] if message else "Agent 已结束"
        self.update_status("ready" if success else "error", first_line)
        self._running_session_id = None
        if self._close_pending:
            QTimer.singleShot(0, self.close)

    def _disconnect_confirmation(self) -> None:
        """Disconnect the current worker decision slot if connected."""

        if self.worker is None:
            return
        try:
            self.confirm_signal.disconnect(self.worker.resolve_write_confirmation)
        except RuntimeError:
            pass

    @Slot(str)
    def _mark_confirmation_pending(self, path: str) -> None:
        """Show a persistent conversation and input-adjacent waiting cue."""

        self._awaiting_confirmation = True
        self._pending_write_path = path
        state = self._tab_previews.get(path)
        if state is not None:
            state["pending"] = True
        self.apply_button.setEnabled(True)
        self.reject_button.setEnabled(True)
        self.decision_widget.setVisible(True)
        session_id = self._running_session_id or self.session_store.active_id
        self.session_store.add_message(
            "system",
            "✋ 需要您确认才能继续",
            conversation_id=session_id,
            waiting=True,
        )
        if session_id == self.session_store.active_id:
            self._render_active_session()
        self.waiting_indicator.setVisible(True)
        self.waiting_timer.start()
        self.update_status("running", f"等待确认修改：{path}")

    @Slot()
    def _apply_pending_change(self) -> None:
        """Approve the visible Diff and release the blocked worker."""

        if not self._awaiting_confirmation:
            return
        path = self._pending_write_path
        self._awaiting_confirmation = False
        self._pending_write_path = ""
        self.apply_button.setEnabled(False)
        self.reject_button.setEnabled(False)
        self.decision_widget.setVisible(False)
        self._clear_diff_decoration(path, clear_code=False)
        session_id = self._running_session_id or self.session_store.active_id
        self.session_store.update_waiting_message(
            f"✅ 已允许修改：{path}",
            conversation_id=session_id,
        )
        if session_id == self.session_store.active_id:
            self._render_active_session()
        self._stop_waiting_indicator()
        self.update_status("running", "已确认，Agent 继续执行")
        self.confirm_signal.emit(True)

    @Slot()
    def _reject_pending_change(self) -> None:
        """Reject the visible Diff, clear its editor, and return user_aborted."""

        if not self._awaiting_confirmation:
            return
        path = self._pending_write_path
        self._awaiting_confirmation = False
        self._pending_write_path = ""
        self.apply_button.setEnabled(False)
        self.reject_button.setEnabled(False)
        self.decision_widget.setVisible(False)
        self._clear_diff_decoration(path, clear_code=False)
        session_id = self._running_session_id or self.session_store.active_id
        self.session_store.update_waiting_message(
            f"⛔ 已拒绝修改：{path}",
            conversation_id=session_id,
        )
        if session_id == self.session_store.active_id:
            self._render_active_session()
        self._stop_waiting_indicator()
        self.update_status("running", "已拒绝，Agent 将重新思考")
        self.confirm_signal.emit(False)

    @Slot()
    def _blink_waiting_indicator(self) -> None:
        """Animate a small text marker without blocking the GUI thread."""

        self._waiting_blink_on = not self._waiting_blink_on
        marker = "⏳" if self._waiting_blink_on else "✋"
        self.waiting_indicator.setText(f"{marker} Agent 正在等待您的操作...")

    def _stop_waiting_indicator(self) -> None:
        """Stop and hide the waiting animation after a decision."""

        self.waiting_timer.stop()
        self.waiting_indicator.setVisible(False)
        self._waiting_blink_on = False

    def _refresh_session_combo(self) -> None:
        """Rebuild session titles while preserving the active identifier."""

        self.session_combo.blockSignals(True)
        self.session_combo.clear()
        active_index = 0
        for index, conversation in enumerate(self.session_store.conversations):
            self.session_combo.addItem(conversation.title, conversation.id)
            if conversation.id == self.session_store.active_id:
                active_index = index
        self.session_combo.setCurrentIndex(active_index)
        self.session_combo.blockSignals(False)

    @Slot()
    def _new_session(self) -> None:
        """Create and display a new session without touching old histories."""

        self.session_store.create()
        self._refresh_session_combo()
        self._render_active_session()

    @Slot()
    def _delete_active_session(self) -> None:
        """Delete the complete active historical conversation after confirmation."""

        conversation = self.session_store.active
        decision = QMessageBox.question(
            self,
            "删除历史对话",
            f"确定永久删除“{conversation.title}”及其全部消息和日志吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if decision != QMessageBox.StandardButton.Yes:
            return
        if not self.session_store.delete_conversation(conversation.id):
            return
        self._refresh_session_combo()
        self._render_active_session()
        self.update_status("ready", "历史对话已删除")

    @Slot(int)
    def _switch_session(self, index: int) -> None:
        """Switch both message and log views to the selected session."""

        conversation_id = self.session_combo.itemData(index)
        if isinstance(conversation_id, str) and self.session_store.activate(conversation_id):
            self._render_active_session()

    def _render_active_session(self) -> None:
        """Render modern left/right message bubbles and session activity."""

        conversation = self.session_store.active
        self.conversation_view.set_messages(
            conversation.id,
            conversation.messages,
        )
        self.log_view.clear()
        for log in conversation.logs:
            if log.get("step") == 0:
                self.log_view.append(self._format_log_html(log))
        self._render_process(conversation)

    @Slot(str, int)
    def _delete_conversation_message(
        self,
        conversation_id: str,
        message_index: int,
    ) -> None:
        """Permanently delete a message requested by its native bubble."""

        if not self.session_store.delete_message(conversation_id, message_index):
            return
        self._refresh_session_combo()
        if conversation_id == self.session_store.active_id:
            self._render_active_session()

    @Slot(object, str)
    def _store_agent_snapshot(self, snapshot: object, timestamp: str) -> None:
        """Replace the single snapshot slot with the run's initial state."""

        if not isinstance(snapshot, dict):
            return
        if not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in snapshot.items()
        ):
            return
        discard_workspace_snapshot(self.snapshot_data)
        self.snapshot_data = dict(snapshot)
        self.snapshot_timestamp = timestamp
        self._refresh_snapshot_label()

    @Slot()
    def _save_snapshot(self) -> None:
        """Overwrite the one snapshot slot and update its timestamp."""

        if self.workspace_root is None:
            self.update_status("error", "请先打开工作区")
            return
        if self.worker is not None and self.worker.isRunning():
            self.update_status("error", "Agent 运行时不能覆盖快照")
            return
        try:
            new_snapshot = save_workspace_snapshot(self.workspace_root)
        except (OSError, RuntimeError, ValueError) as exc:
            self.update_status("error", f"快照保存失败：{exc}")
            return
        discard_workspace_snapshot(self.snapshot_data)
        self.snapshot_data = new_snapshot
        self.snapshot_timestamp = self._now_timestamp()
        file_count = sum(1 for path in new_snapshot if path != SNAPSHOT_META_KEY)
        self._refresh_snapshot_label()
        self.update_status("ready", f"已保存 {file_count} 个文件的快照")

    @Slot()
    def _rollback_snapshot(self) -> None:
        """Restore the latest snapshot slot and show its precise timestamp."""

        if self.worker is not None and self.worker.isRunning():
            self.update_status("error", "Agent 运行时不能回退")
            return
        if not self.snapshot_data or self.snapshot_timestamp is None:
            self.update_status("error", "尚未保存快照")
            return
        if not rollback_to_snapshot(self.snapshot_data):
            self.update_status("error", "快照回退失败")
            return
        self._refresh_open_tabs_from_disk()
        message = f"✅ 已退回至 [{self.snapshot_timestamp}] 的快照"
        self.update_status("ready", message)
        self.update_log(0, "↩", "回退", "success", message)

    @staticmethod
    def _now_timestamp() -> str:
        """Return a local timestamp precise to one second."""

        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _refresh_snapshot_label(self) -> None:
        """Display the active single-slot snapshot timestamp."""

        value = self.snapshot_timestamp or "未保存"
        self.snapshot_label.setText(f"快照时间：{value}")

    @Slot()
    def _open_workspace(self) -> None:
        """Select and activate an existing workspace directory."""

        selected = QFileDialog.getExistingDirectory(
            self,
            "选择工作区",
            str(self.workspace_root or Path.cwd()),
        )
        if not selected:
            return
        try:
            workspace = Path(selected).resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as exc:
            self.update_status("error", f"工作区无效：{exc}")
            return
        if not workspace.is_dir():
            self.update_status("error", "工作区必须是目录")
            return
        self._activate_workspace(workspace)

    def _activate_workspace(self, workspace: Path) -> None:
        """Switch every workspace-dependent component to one directory."""

        if self.worker is not None and self.worker.isRunning():
            self.update_status("error", "Agent 运行时不能切换工作区")
            return
        if self._has_pending_diffs():
            if not self._confirm_discard_pending("workspace"):
                return
            for file_path in tuple(self._tab_previews):
                if self._tab_has_pending_diff(file_path):
                    self._clear_diff_decoration(file_path, clear_code=False)
        if not self._close_all_code_tabs():
            self.update_status("ready", "已取消切换工作区")
            return
        try:
            self._synchronize_process_cwd(workspace)
        except OSError as exc:
            self.update_status("error", f"无法切换工作目录：{exc}")
            return
        discard_workspace_snapshot(self.snapshot_data)
        self.snapshot_data = {}
        self.snapshot_timestamp = None
        self.workspace_root = workspace
        self._populate_workspace_files()
        self._refresh_workspace_label()
        self._refresh_snapshot_label()
        self.update_status("ready", "工作区已打开")

    @staticmethod
    def _synchronize_process_cwd(workspace: Path) -> None:
        """Synchronize the process/terminal working directory with the workspace."""

        os.chdir(workspace)

    def _refresh_workspace_label(self) -> None:
        """Show the active workspace path in the toolbar."""

        value = str(self.workspace_root) if self.workspace_root is not None else "未选择"
        self.workspace_label.setText(f"工作区：{value}")
        self.workspace_label.setToolTip(value)

    def _populate_workspace_files(self) -> None:
        """Refresh the filesystem tree and its compatibility file-state set."""

        self.workspace_files.clear()
        if self.workspace_root is None:
            self.workspace_tree.setRootIndex(QModelIndex())
            self.workspace_tree.setVisible(False)
            self.workspace_empty_label.setVisible(True)
            return
        self.workspace_empty_label.setVisible(False)
        self.workspace_tree.setVisible(True)
        root_index = self.workspace_model.setRootPath(str(self.workspace_root))
        self.workspace_tree.setRootIndex(root_index)
        excluded = {".git", ".venv", "venv", "__pycache__", ".pytest_cache"}
        for candidate in sorted(self.workspace_root.rglob("*")):
            try:
                relative = candidate.relative_to(self.workspace_root)
            except ValueError:
                continue
            if any(part in excluded for part in relative.parts):
                continue
            if candidate.is_symlink() or not candidate.is_file():
                continue
            self.workspace_files.add(relative.as_posix())
            if len(self.workspace_files) >= 2_000:
                break

    @Slot(QModelIndex)
    def _activate_workspace_index(self, index: QModelIndex) -> None:
        """Toggle a directory or open a UTF-8 file from the tree."""

        if self.workspace_root is None or not index.isValid():
            return
        try:
            model_path = Path(self.workspace_model.filePath(index)).resolve(strict=True)
            relative_path = model_path.relative_to(self.workspace_root).as_posix()
            target = resolve_in_workspace(
                self.workspace_root,
                relative_path,
                must_exist=True,
            )
            if target.is_dir():
                self.workspace_tree.setExpanded(
                    index,
                    not self.workspace_tree.isExpanded(index),
                )
                return
            content = target.read_text(encoding="utf-8")
        except (OSError, RuntimeError, UnicodeError, ValueError) as exc:
            self.update_status("error", f"无法打开文件：{exc}")
            return
        self.update_code(relative_path, content)

    @Slot(QPoint)
    def _show_workspace_context_menu(self, position: QPoint) -> None:
        """Offer safe create/delete operations for the selected tree location."""

        if self.workspace_root is None:
            return
        index = self.workspace_tree.indexAt(position)
        if index.isValid():
            self.workspace_tree.setCurrentIndex(index)
        menu = QMenu(self.workspace_tree)
        create_file = menu.addAction("新建文件")
        create_folder = menu.addAction("新建文件夹")
        delete_entry = menu.addAction("删除")
        delete_entry.setEnabled(index.isValid())
        selected = menu.exec(self.workspace_tree.viewport().mapToGlobal(position))
        if selected is create_file:
            self._prompt_create_workspace_entry(is_directory=False)
        elif selected is create_folder:
            self._prompt_create_workspace_entry(is_directory=True)
        elif selected is delete_entry:
            self._confirm_delete_workspace_entry()

    def _selected_workspace_directory(self) -> Path | None:
        """Return the selected directory, or the parent of a selected file."""

        if self.workspace_root is None:
            return None
        index = self.workspace_tree.currentIndex()
        if not index.isValid():
            return self.workspace_root
        try:
            selected = Path(self.workspace_model.filePath(index)).resolve(strict=True)
            selected.relative_to(self.workspace_root)
        except (OSError, RuntimeError, ValueError):
            return None
        return selected if selected.is_dir() else selected.parent

    def _prompt_create_workspace_entry(self, *, is_directory: bool) -> None:
        """Request a basename and create it under the selected directory."""

        parent = self._selected_workspace_directory()
        if parent is None:
            self.update_status("error", "没有可用的目标文件夹")
            return
        label = "文件夹名" if is_directory else "文件名"
        name, accepted = QInputDialog.getText(self, f"新建{label}", label)
        if not accepted or not name.strip():
            return
        try:
            created = self._create_workspace_entry(parent, name, is_directory)
        except (OSError, RuntimeError, ValueError) as exc:
            self.update_status("error", f"创建失败：{exc}")
            return
        self._populate_workspace_files()
        self.update_status("ready", f"已创建：{created.name}")

    def _create_workspace_entry(
        self,
        parent: Path,
        name: str,
        is_directory: bool,
    ) -> Path:
        """Create one validated empty file or directory inside the workspace."""

        if self.workspace_root is None:
            raise ValueError("workspace is not selected")
        clean_name = name.strip()
        if (
            not clean_name
            or clean_name in {".", ".."}
            or Path(clean_name).name != clean_name
            or "/" in clean_name
            or "\\" in clean_name
            or "\x00" in clean_name
        ):
            raise ValueError("名称必须是不含路径分隔符的单个名称")
        try:
            relative_parent = parent.resolve(strict=True).relative_to(
                self.workspace_root
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValueError("目标文件夹不在工作区内") from exc
        target = resolve_in_workspace(
            self.workspace_root,
            (relative_parent / clean_name).as_posix(),
            must_exist=False,
        )
        if target.exists():
            raise FileExistsError(f"{clean_name} 已存在")
        if is_directory:
            target.mkdir()
        else:
            target.touch(exist_ok=False)
        return target

    def _confirm_delete_workspace_entry(self) -> None:
        """Confirm and delete the selected file or directory recursively."""

        if self.workspace_root is None:
            return
        index = self.workspace_tree.currentIndex()
        if not index.isValid():
            return
        candidate = Path(self.workspace_model.filePath(index))
        kind = "文件夹及其全部内容" if candidate.is_dir() else "文件"
        decision = QMessageBox.question(
            self,
            "确认删除",
            f"确定删除{kind} {candidate.name} 吗？此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if decision != QMessageBox.StandardButton.Yes:
            return
        try:
            deleted = self._delete_workspace_entry(candidate)
        except (OSError, RuntimeError, ValueError) as exc:
            self.update_status("error", f"删除失败：{exc}")
            return
        self._populate_workspace_files()
        self.update_status("ready", f"已删除：{deleted.name}")

    def _delete_workspace_entry(self, candidate: Path) -> Path:
        """Delete one exact validated workspace child; never delete the root."""

        if self.workspace_root is None:
            raise ValueError("workspace is not selected")
        try:
            resolved = candidate.resolve(strict=True)
            relative = resolved.relative_to(self.workspace_root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValueError("删除目标不在工作区内") from exc
        if not relative.parts:
            raise ValueError("不能删除工作区根目录")
        validated = resolve_in_workspace(
            self.workspace_root,
            relative.as_posix(),
            must_exist=True,
        )
        if validated.is_dir():
            shutil.rmtree(validated)
        else:
            validated.unlink()
        return validated

    @Slot()
    def _toggle_workspace_panel(self) -> None:
        """Collapse or restore the workspace panel and its saved width."""

        if not self._workspace_collapsed:
            self._workspace_saved_width = max(self.workspace_panel.width(), 120)
            self.workspace_panel.setVisible(False)
            self._workspace_collapsed = True
            self.workspace_toggle_button.setText("▶ 工作区")
            return
        self.workspace_panel.setVisible(True)
        self._workspace_collapsed = False
        sizes = self.splitter.sizes()
        center = sizes[1] if len(sizes) > 1 else 620
        right = sizes[2] if len(sizes) > 2 else 460
        self.splitter.setSizes([self._workspace_saved_width, center, right])
        self.workspace_toggle_button.setText("◀ 工作区")

    def _expand_workspace_panel(self) -> None:
        """Automatically reveal the workspace panel after an accepted import."""

        if self._workspace_collapsed:
            self._toggle_workspace_panel()

    @Slot(int, int)
    def _remember_workspace_width(self, position: int, index: int) -> None:
        """Remember user resizing for the next expand operation."""

        _ = (position, index)
        if not self._workspace_collapsed:
            width = self.workspace_panel.width()
            if 120 <= width <= 400:
                self._workspace_saved_width = width

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        """Accept a drag containing at least one local regular file."""

        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if any(Path(url.toLocalFile()).is_file() for url in urls if url.isLocalFile()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        """Ask whether each UTF-8 file should be imported or previewed only."""

        handled = 0
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        for url in urls:
            if not url.isLocalFile():
                continue
            try:
                source = Path(url.toLocalFile()).resolve(strict=True)
                if not source.is_file():
                    continue
                if source.stat().st_size > DEFAULT_MAX_WRITE_BYTES:
                    raise ValueError("文件超过预览与导入大小限制")
                content = source.read_text(encoding="utf-8")
            except (OSError, RuntimeError, UnicodeError, ValueError) as exc:
                self.update_status("error", f"无法读取拖入文件：{exc}")
                continue

            decision = QMessageBox.question(
                self,
                "加入工作区",
                f"是否将 {source.name} 加入工作区？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if decision == QMessageBox.StandardButton.Yes:
                if not self._import_workspace_file(source, content):
                    continue
            self.update_code(source.name, content)
            handled += 1

        if handled:
            event.acceptProposedAction()
        else:
            event.ignore()

    def _import_workspace_file(self, source: Path, content: str) -> bool:
        """Atomically import one dropped file and update workspace state."""

        if self.workspace_root is None:
            self.update_status("error", "请先打开工作区；文件仅可预览")
            return False
        if self.worker is not None and self.worker.isRunning():
            self.update_status("error", "Agent 运行时不能导入文件")
            return False
        try:
            destination = resolve_in_workspace(
                self.workspace_root,
                source.name,
                must_exist=False,
            )
            expected_hash = (
                sha256_file_streaming(destination) if destination.is_file() else ""
            )
            result = write_file(
                self.workspace_root,
                source.name,
                content,
                expected_hash,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            self.update_status("error", f"导入失败：{exc}")
            return False
        if result.get("ok") is not True:
            error = result.get("error")
            reason = error.get("message", "未知错误") if isinstance(error, dict) else "未知错误"
            self.update_status("error", f"导入失败：{reason}")
            return False
        self.workspace_files.add(source.name)
        self._refresh_workspace_list()
        self._expand_workspace_panel()
        self.update_log(0, "↓", "导入", "success", f"文件 {source.name} 已导入工作区")
        self.update_status("ready", f"文件 {source.name} 已导入工作区")
        return True

    def _refresh_workspace_list(self) -> None:
        """Refresh the filesystem-backed tree after a local change."""

        self._populate_workspace_files()

    def _refresh_open_tabs_from_disk(self) -> None:
        """Reload open workspace tabs after rollback and clear removed files."""

        if self.workspace_root is None:
            return
        for file_path in tuple(self._tab_editors):
            try:
                target = resolve_in_workspace(
                    self.workspace_root,
                    file_path,
                    must_exist=True,
                )
                content = target.read_text(encoding="utf-8")
            except (OSError, RuntimeError, UnicodeError, ValueError):
                state = self._tab_previews.get(file_path)
                if state is not None:
                    state.update({"code": "", "diff": "", "additions": 0, "deletions": 0})
                self._tab_editors[file_path].clear()
                continue
            self.update_code(file_path, content)
        self._populate_workspace_files()

    def _close_all_code_tabs(self) -> bool:
        """Close all previews, stopping safely if a dirty-tab prompt is cancelled."""

        while self.code_tabs.count():
            previous_count = self.code_tabs.count()
            self._close_code_tab(0)
            if self.code_tabs.count() == previous_count:
                return False
        return True

    @Slot()
    def _toggle_theme(self) -> None:
        """Switch and persist the global light/dark stylesheet."""

        self.theme_name = "light" if self.theme_name == "dark" else "dark"
        self.settings.setValue("ui/theme", self.theme_name)
        self.settings.sync()
        self._apply_theme()

    def _apply_theme(self) -> None:
        """Apply the selected stylesheet and re-render inline HTML colors."""

        stylesheet, colors = get_theme(self.theme_name)
        self.theme_colors = colors
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(stylesheet)
        self.theme_button.setText("☀ 亮色" if self.theme_name == "dark" else "🌙 暗色")
        self._render_active_session()
        for file_path in self._tab_editors:
            self._render_code_tab(file_path)
        self._refresh_tool_status_style()
        current_status = self.statusBar().currentMessage() or "就绪"
        state = "running" if self.worker is not None and self.worker.isRunning() else "ready"
        self.update_status(state, current_status)

    @Slot(int)
    def _set_thinking_mode(self, index: int) -> None:
        """Map the user-facing quick/deep choice to the core Agent mode."""

        selected = self.thinking_mode_combo.itemData(index)
        self.thinking_mode = selected if selected in {"quick", "deep"} else "quick"
        self.mode = "goal" if self.thinking_mode == "deep" else "auto"
        self.settings.setValue("ui/thinking_mode", self.thinking_mode)
        self.settings.sync()
        self._render_process(self.session_store.active)

    @Slot(bool)
    def _set_interactive_confirmation(self, checked: bool) -> None:
        """Keep GUI Agent writes on the mandatory Diff-approval path."""

        self.interactive_confirmation = True
        if not checked:
            self.interactive_action.blockSignals(True)
            self.interactive_action.setChecked(True)
            self.interactive_action.blockSignals(False)
        self.statusBar().showMessage("Agent 文件修改审批已开启")

    def closeEvent(self, event: QCloseEvent) -> None:
        """Persist UI state and defer destruction until the worker exits."""

        if self._has_pending_diffs():
            if not self._confirm_discard_pending("window"):
                event.ignore()
                return
            if self._awaiting_confirmation:
                self._reject_pending_change()
            else:
                for file_path in tuple(self._tab_previews):
                    if self._tab_has_pending_diff(file_path):
                        self._clear_diff_decoration(file_path, clear_code=False)
        self.session_store.save()
        self.settings.setValue("ui/theme", self.theme_name)
        self.settings.setValue("ui/thinking_mode", self.thinking_mode)
        self.settings.sync()
        if self.worker is not None and self.worker.isRunning():
            self.worker.requestInterruption()
            self._close_pending = True
            self.update_status("running", "正在等待 Agent 安全停止……")
            event.ignore()
            return
        discard_workspace_snapshot(self.snapshot_data)
        self.snapshot_data = {}
        self.loading_timer.stop()
        self.waiting_timer.stop()
        if self._launch_cwd.is_dir():
            try:
                os.chdir(self._launch_cwd)
            except OSError:
                pass
        super().closeEvent(event)
