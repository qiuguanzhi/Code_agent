"""Native reusable widgets for the desktop conversation interface."""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class MessageBubble(QWidget):
    """One genuinely rounded native message bubble with a delete control."""

    delete_requested = Signal(str, int)

    def __init__(
        self,
        conversation_id: str,
        message_index: int,
        role: str,
        content: str,
        *,
        deletable: bool,
        parent: QWidget | None = None,
    ) -> None:
        """Create one left- or right-aligned selectable message."""

        super().__init__(parent)
        self.conversation_id = conversation_id
        self.message_index = message_index
        self.role = role
        self._uses_wide_layout = len(content) >= 120

        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 5, 8, 5)
        outer.setSpacing(0)

        self.bubble_frame = QFrame(self)
        self.bubble_frame.setObjectName(
            "userBubble"
            if role == "user"
            else "assistantBubble"
            if role == "assistant"
            else "systemBubble"
        )
        self.bubble_frame.setMinimumWidth(300)
        bubble_layout = QVBoxLayout(self.bubble_frame)
        bubble_layout.setContentsMargins(14, 10, 12, 11)
        bubble_layout.setSpacing(5)

        header = QHBoxLayout()
        title = QLabel(
            "你" if role == "user" else "Agent" if role == "assistant" else "系统",
            self.bubble_frame,
        )
        title.setObjectName("messageRoleLabel")
        header.addWidget(title)
        header.addStretch(1)
        if deletable:
            delete_button = QToolButton(self.bubble_frame)
            delete_button.setObjectName("messageDeleteButton")
            delete_button.setText("×")
            delete_button.setToolTip("删除这条消息")
            delete_button.clicked.connect(
                lambda: self.delete_requested.emit(
                    self.conversation_id,
                    self.message_index,
                )
            )
            header.addWidget(delete_button)
        bubble_layout.addLayout(header)

        self.content_label = QLabel(content, self.bubble_frame)
        self.content_label.setObjectName("messageContent")
        self.content_label.setWordWrap(True)
        self.content_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        bubble_layout.addWidget(self.content_label)

        if role == "user":
            outer.addStretch(1)
            outer.addWidget(self.bubble_frame, 0, Qt.AlignmentFlag.AlignRight)
        else:
            outer.addWidget(self.bubble_frame, 0, Qt.AlignmentFlag.AlignLeft)
            outer.addStretch(1)

    def set_width_constraints(self, width: int) -> None:
        """Apply a responsive 85%-parent cap with a readable normal minimum."""

        safe_width = max(80, width)
        minimum = 300
        if self._uses_wide_layout:
            minimum = max(minimum, math.ceil(safe_width * (0.80 / 0.85)))
        self.bubble_frame.setMinimumWidth(min(minimum, safe_width))
        self.bubble_frame.setMaximumWidth(safe_width)
        if self._uses_wide_layout:
            self.content_label.setFixedWidth(max(40, safe_width - 26))
            self.content_label.adjustSize()
            self.bubble_frame.layout().activate()
            self.bubble_frame.adjustSize()


class ConversationScrollArea(QScrollArea):
    """Scrollable message bubbles with a stable testable plain-text surface."""

    delete_requested = Signal(str, int)
    BUBBLE_WIDTH_RATIO = 0.85

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create the scroll container and its vertically stacked bubble host."""

        super().__init__(parent)
        self.setObjectName("conversationView")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._content = QWidget(self)
        self._content.setObjectName("conversationContent")
        self._layout = QVBoxLayout(self._content)
        self._layout.setContentsMargins(4, 8, 4, 8)
        self._layout.setSpacing(3)
        self._layout.addStretch(1)
        self.setWidget(self._content)
        self._plain_messages: list[str] = []
        self.bubbles: list[MessageBubble] = []

    def set_messages(
        self,
        conversation_id: str,
        messages: list[dict[str, Any]],
    ) -> None:
        """Replace visible bubbles while preserving only trusted text fields."""

        self._clear_bubbles()
        self._plain_messages = []
        for index, message in enumerate(messages):
            role = str(message.get("role", "system"))
            content = str(message.get("content", ""))
            bubble = MessageBubble(
                conversation_id,
                index,
                role,
                content,
                deletable=role in {"user", "assistant"},
                parent=self._content,
            )
            bubble.delete_requested.connect(self.delete_requested)
            bubble.set_width_constraints(self._bubble_width_limit())
            self._layout.insertWidget(self._layout.count() - 1, bubble)
            self.bubbles.append(bubble)
            self._plain_messages.append(content)
        scrollbar = self.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Keep bubbles readable while using available conversation width."""

        super().resizeEvent(event)
        width_limit = self._bubble_width_limit()
        for bubble in self.bubbles:
            bubble.set_width_constraints(width_limit)

    def _bubble_width_limit(self) -> int:
        """Return 85% of the viewport, capped at the desktop reading width."""

        viewport_width = max(1, self.viewport().width())
        return max(80, int(viewport_width * self.BUBBLE_WIDTH_RATIO))

    def toPlainText(self) -> str:
        """Return visible message text for accessibility and regression tests."""

        return "\n".join(self._plain_messages)

    def _clear_bubbles(self) -> None:
        """Remove old bubble widgets before rendering another conversation."""

        for bubble in self.bubbles:
            self._layout.removeWidget(bubble)
            bubble.deleteLater()
        self.bubbles.clear()


class FileMentionPopup(QFrame):
    """Popup list used to insert fuzzy-matched workspace file mentions."""

    mention_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create an initially hidden, keyboard-friendly file list."""

        super().__init__(parent)
        self.setObjectName("fileMentionPopup")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self.list_widget = QListWidget(self)
        self.list_widget.setObjectName("fileMentionList")
        self.list_widget.itemClicked.connect(self._select_item)
        self.list_widget.itemActivated.connect(self._select_item)
        layout.addWidget(self.list_widget)
        self._all_paths: list[str] = []
        self.hide()

    def set_files(self, paths: list[str]) -> None:
        """Replace the available ordinary workspace-file paths."""

        self._all_paths = sorted(dict.fromkeys(paths), key=str.casefold)

    def show_matches(self, anchor: QLineEdit, query: str) -> None:
        """Filter paths fuzzily and show the popup below the input control."""

        normalized = query.casefold()
        matches = [
            path
            for path in self._all_paths
            if self._fuzzy_match(normalized, path.casefold())
        ]
        self.list_widget.clear()
        if self._fuzzy_match(normalized, "workplace"):
            workplace_item = QListWidgetItem("@workplace  —  引用全部工作区文件")
            workplace_item.setData(Qt.ItemDataRole.UserRole, "workplace")
            self.list_widget.addItem(workplace_item)
        for path in matches:
            item = QListWidgetItem(path)
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.list_widget.addItem(item)
        if self.list_widget.count() == 0:
            self.hide()
            return
        self.list_widget.setCurrentRow(0)
        width = max(320, anchor.width())
        height = min(280, max(72, self.list_widget.count() * 28 + 12))
        self.resize(width, height)
        parent = self.parentWidget()
        position = (
            anchor.mapTo(parent, QPoint(0, anchor.height() + 2))
            if parent is not None
            else QPoint(0, anchor.height() + 2)
        )
        self.move(position)
        self.show()
        self.raise_()

    def choose_current(self) -> bool:
        """Emit the current selection, returning whether an item existed."""

        item = self.list_widget.currentItem()
        if item is None:
            return False
        self._select_item(item)
        return True

    def move_current(self, offset: int) -> None:
        """Move popup selection by one row while clamping to valid bounds."""

        count = self.list_widget.count()
        if count == 0:
            return
        row = max(0, min(count - 1, self.list_widget.currentRow() + offset))
        self.list_widget.setCurrentRow(row)

    @staticmethod
    def _fuzzy_match(query: str, candidate: str) -> bool:
        """Match a substring first, then fall back to ordered subsequence."""

        if not query or query in candidate:
            return True
        iterator = iter(candidate)
        return all(any(character == value for value in iterator) for character in query)

    def _select_item(self, item: QListWidgetItem) -> None:
        """Emit the stable path stored on an activated row and close."""

        value = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(value, str):
            self.mention_selected.emit(value)
        self.hide()
