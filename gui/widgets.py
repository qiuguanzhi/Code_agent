"""Small native Qt widgets shared by the desktop interface."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QLabel, QTabWidget, QWidget


class CodeTabWidget(QTabWidget):
    """A tab widget with a disabled black placeholder over its empty tab bar."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create and position the non-interactive empty-state block."""

        super().__init__(parent)
        self.empty_placeholder = QLabel("未打开文件", self)
        self.empty_placeholder.setObjectName("emptyTabPlaceholder")
        self.empty_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_placeholder.setEnabled(False)
        self.empty_placeholder.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )
        self.refresh_empty_placeholder()

    def refresh_empty_placeholder(self) -> None:
        """Show the placeholder exactly while no file tabs exist."""

        self.empty_placeholder.setVisible(self.count() == 0)
        self._position_empty_placeholder()
        if self.empty_placeholder.isVisible():
            self.empty_placeholder.raise_()

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Keep the placeholder aligned with the native tab-bar strip."""

        super().resizeEvent(event)
        self._position_empty_placeholder()

    def _position_empty_placeholder(self) -> None:
        """Cover only the tab-bar strip, leaving the editor pane untouched."""

        tab_height = max(self.tabBar().sizeHint().height(), 32)
        self.empty_placeholder.setGeometry(0, 0, self.width(), tab_height)
