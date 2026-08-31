"""Native dialog for viewing and enabling runtime skills."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from skills.registry import SkillRegistry


class SkillManagerDialog(QDialog):
    """Display discovered skills and edit their enabled state."""

    def __init__(
        self,
        registry: SkillRegistry,
        parent: QWidget | None = None,
    ) -> None:
        """Build a modal, refreshable list from one registry."""

        super().__init__(parent)
        self.registry = registry
        self.setObjectName("skillManagerDialog")
        self.setWindowTitle("技能管理")
        self.resize(620, 420)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("可调用技能（取消勾选后不会暴露给模型）", self))
        self.skill_list = QListWidget(self)
        self.skill_list.setObjectName("skillList")
        layout.addWidget(self.skill_list, 1)
        controls = QHBoxLayout()
        self.refresh_button = QPushButton("刷新技能", self)
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
        self.refresh()

    def refresh(self) -> None:
        """Rebuild the list while retaining current checkbox choices."""

        selected = self.enabled_names() if self.skill_list.count() else self.registry.enabled_names()
        self.registry = SkillRegistry.discover_builtin(enabled_names=selected)
        self.skill_list.clear()
        for skill in self.registry.list_skills():
            permissions = ", ".join(sorted(skill.required_permissions)) or "无"
            risk = " · 高风险，执行前需确认" if skill.high_risk else ""
            item = QListWidgetItem(
                f"{skill.name}\n{skill.description}\n权限：{permissions}{risk}"
            )
            item.setData(Qt.ItemDataRole.UserRole, skill.name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked
                if skill.name in selected
                else Qt.CheckState.Unchecked
            )
            self.skill_list.addItem(item)

    def enabled_names(self) -> frozenset[str]:
        """Return all names whose checkbox is currently selected."""

        names: set[str] = set()
        for row in range(self.skill_list.count()):
            item = self.skill_list.item(row)
            name = item.data(Qt.ItemDataRole.UserRole)
            if item.checkState() == Qt.CheckState.Checked and isinstance(name, str):
                names.add(name)
        return frozenset(names)
