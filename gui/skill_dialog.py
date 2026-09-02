"""Native dialogs for manually creating, deleting, and enabling Skills."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from skills.base import Skill
from gui.widgets import AddSkillDialog
from skills.registry import SkillCodeSafetyError, SkillRegistry


class SkillManagerDialog(QDialog):
    """Manage persisted user Skills and model-visible enablement."""

    skill_added = Signal(str)
    skill_deleted = Signal(str)

    def __init__(
        self,
        registry: SkillRegistry,
        parent: QWidget | None = None,
    ) -> None:
        """Build a frameless, draggable two-column manager."""

        super().__init__(parent)
        self.registry = registry
        self._drag_offset: QPoint | None = None
        self.setObjectName("skillManagerDialog")
        self.setWindowTitle("Skill 管理")
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowOpacity(0.97)
        self.resize(760, 480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        header = QHBoxLayout()
        title = QLabel("🧩 Skill 管理", self)
        title.setObjectName("skillManagerTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.close_button = QPushButton("×", self)
        self.close_button.setObjectName("skillManagerCloseButton")
        self.close_button.setFixedSize(30, 30)
        self.close_button.clicked.connect(self.reject)
        header.addWidget(self.close_button)
        layout.addLayout(header)

        hint = QLabel(
            "勾选后向模型开放。手动代码在受限命名空间加载，所有运行时能力仍须通过 "
            "Agent 工具权限检查。",
            self,
        )
        hint.setObjectName("skillSecurityHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        body = QHBoxLayout()
        self.skill_list = QListWidget(self)
        self.skill_list.setObjectName("skillList")
        self.skill_list.currentItemChanged.connect(self._show_selected_skill)
        body.addWidget(self.skill_list, 3)

        detail_panel = QWidget(self)
        detail_panel.setObjectName("skillDetailPanel")
        detail_layout = QVBoxLayout(detail_panel)
        detail_title = QLabel("Skill 详情", detail_panel)
        detail_title.setObjectName("skillDetailTitle")
        detail_layout.addWidget(detail_title)
        self.name_value = QLabel("未选择", detail_panel)
        self.name_value.setObjectName("skillNameValue")
        self.description_value = QLabel("", detail_panel)
        self.description_value.setObjectName("skillDescriptionValue")
        self.description_value.setWordWrap(True)
        self.permissions_value = QLabel("权限：无", detail_panel)
        self.permissions_value.setObjectName("skillPermissionsValue")
        self.permissions_value.setWordWrap(True)
        self.origin_value = QLabel("类型：-", detail_panel)
        self.origin_value.setObjectName("skillOriginValue")
        self.origin_value.setWordWrap(True)
        detail_layout.addWidget(self.name_value)
        detail_layout.addWidget(self.description_value)
        detail_layout.addWidget(self.permissions_value)
        detail_layout.addWidget(self.origin_value)
        detail_layout.addStretch(1)
        body.addWidget(detail_panel, 4)
        layout.addLayout(body, 1)

        controls = QHBoxLayout()
        self.add_button = QPushButton("添加 Skill", self)
        self.add_button.setObjectName("addSkillButton")
        self.add_button.clicked.connect(self.add_skill)
        controls.addWidget(self.add_button)
        self.delete_button = QPushButton("删除", self)
        self.delete_button.setObjectName("deleteSkillButton")
        self.delete_button.setEnabled(False)
        self.delete_button.clicked.connect(self.delete_selected_skill)
        controls.addWidget(self.delete_button)
        self.refresh_button = QPushButton("刷新技能", self)
        self.refresh_button.setObjectName("refreshSkillButton")
        self.refresh_button.clicked.connect(self.refresh)
        controls.addWidget(self.refresh_button)
        controls.addStretch(1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        controls.addWidget(buttons)
        layout.addLayout(controls)
        self._populate_list()

    def refresh(self) -> None:
        """Rescan built-in and persisted user directories."""

        selected = self.enabled_names()
        self.registry = SkillRegistry.discover_all(
            enabled_names=selected,
            user_skill_dir=self.registry.user_skill_dir,
        )
        self._populate_list(selected)

    def _populate_list(self, selected: frozenset[str] | None = None) -> None:
        """Rebuild list rows from the current registry without re-importing files."""

        enabled = self.registry.enabled_names() if selected is None else selected
        current_name = self._selected_name()
        self.skill_list.clear()
        selected_row = -1
        for row, skill in enumerate(self.registry.list_skills()):
            status = "🔒 系统" if skill.built_in else "🧩 用户"
            item = QListWidgetItem(f"{status} · {skill.name}")
            item.setData(Qt.ItemDataRole.UserRole, skill.name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked
                if skill.name in enabled
                else Qt.CheckState.Unchecked
            )
            self.skill_list.addItem(item)
            if skill.name == current_name:
                selected_row = row
        if self.skill_list.count():
            self.skill_list.setCurrentRow(selected_row if selected_row >= 0 else 0)
        else:
            self._show_selected_skill(None, None)

    def enabled_names(self) -> frozenset[str]:
        """Return all names whose checkbox is currently selected."""

        names: set[str] = set()
        for row in range(self.skill_list.count()):
            item = self.skill_list.item(row)
            name = item.data(Qt.ItemDataRole.UserRole)
            if item.checkState() == Qt.CheckState.Checked and isinstance(name, str):
                names.add(name)
        return frozenset(names)

    def apply_enabled_state(self) -> None:
        """Commit checkbox state to the owning registry after dialog acceptance."""

        selected = self.enabled_names()
        for skill in self.registry.list_skills():
            self.registry.set_enabled(skill.name, skill.name in selected)

    def add_skill(self) -> None:
        """Collect, validate, acknowledge, and persist one manual Skill."""

        editor = AddSkillDialog(self)
        if editor.exec() != QDialog.DialogCode.Accepted:
            return
        definition = editor.definition()
        selected = set(self.enabled_names())
        try:
            skill = self.registry.register_from_code(**definition)
        except SkillCodeSafetyError as exc:
            details = "\n".join(f"• {warning}" for warning in exc.warnings)
            decision = QMessageBox.warning(
                self,
                "检测到高风险代码",
                f"{details}\n\n这些名称不会自动获得系统权限。仍要保存此 Skill 吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if decision != QMessageBox.StandardButton.Yes:
                return
            try:
                skill = self.registry.register_from_code(
                    **definition,
                    allow_dangerous=True,
                )
            except (OSError, TypeError, ValueError) as retry_exc:
                QMessageBox.critical(self, "添加失败", str(retry_exc))
                return
        except (OSError, TypeError, ValueError) as exc:
            QMessageBox.critical(self, "添加失败", str(exc))
            return
        selected.add(skill.name)
        self._populate_list(frozenset(selected))
        self._select_name(skill.name)
        self.skill_added.emit(skill.name)

    def delete_selected_skill(self) -> None:
        """Confirm and permanently delete the selected user Skill."""

        skill = self._selected_skill()
        if skill is None:
            return
        if skill.built_in:
            QMessageBox.warning(self, "不可删除", "系统内置 Skill 不可删除。")
            return
        decision = QMessageBox.question(
            self,
            "删除 Skill",
            f"确定要删除 Skill ‘{skill.name}’ 吗？此操作不可恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if decision != QMessageBox.StandardButton.Yes:
            return
        name = skill.name
        selected = set(self.enabled_names())
        try:
            self.registry.unregister(name)
        except (KeyError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "删除失败", str(exc))
            return
        selected.discard(name)
        self._populate_list(frozenset(selected))
        self.skill_deleted.emit(name)

    def _selected_name(self) -> str | None:
        item = self.skill_list.currentItem()
        if item is None:
            return None
        name = item.data(Qt.ItemDataRole.UserRole)
        return name if isinstance(name, str) else None

    def _selected_skill(self) -> Skill | None:
        name = self._selected_name()
        return self.registry.get_skill(name) if name is not None else None

    def _select_name(self, name: str) -> None:
        for row in range(self.skill_list.count()):
            item = self.skill_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == name:
                self.skill_list.setCurrentRow(row)
                return

    def _show_selected_skill(
        self,
        current: QListWidgetItem | None,
        previous: QListWidgetItem | None,
    ) -> None:
        """Render metadata and enforce the built-in deletion guard."""

        _ = previous
        name = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        skill = self.registry.get_skill(name) if isinstance(name, str) else None
        if skill is None:
            self.name_value.setText("未选择")
            self.description_value.clear()
            self.permissions_value.setText("权限：无")
            self.origin_value.setText("类型：-")
            self.delete_button.setEnabled(False)
            return
        permissions = ", ".join(sorted(skill.required_permissions)) or "无"
        risk = "；高风险，执行前需确认" if skill.high_risk else ""
        self.name_value.setText(skill.name)
        self.description_value.setText(skill.description)
        self.permissions_value.setText(f"权限：{permissions}{risk}")
        if skill.built_in:
            self.origin_value.setText("类型：🔒 系统 Skill（不可删除）")
        else:
            source = str(skill.source_path) if skill.source_path is not None else "未知"
            self.origin_value.setText(f"类型：🧩 用户 Skill\n来源：{source}")
        self.delete_button.setEnabled(not skill.built_in)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Begin dragging only from the header strip."""

        if (
            event.button() == Qt.MouseButton.LeftButton
            and event.position().y() <= 56
        ):
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Move the frameless dialog while a header drag is active."""

        if (
            self._drag_offset is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """End a frameless-window drag operation."""

        self._drag_offset = None
        super().mouseReleaseEvent(event)
