"""Native reusable widgets for the desktop conversation interface."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
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

        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 5, 8, 5)
        outer.setSpacing(0)

        bubble = QFrame(self)
        bubble.setObjectName(
            "userBubble"
            if role == "user"
            else "assistantBubble"
            if role == "assistant"
            else "systemBubble"
        )
        bubble.setMaximumWidth(620)
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(14, 10, 12, 11)
        bubble_layout.setSpacing(5)

        header = QHBoxLayout()
        title = QLabel(
            "你" if role == "user" else "Agent" if role == "assistant" else "系统",
            bubble,
        )
        title.setObjectName("messageRoleLabel")
        header.addWidget(title)
        header.addStretch(1)
        if deletable:
            delete_button = QToolButton(bubble)
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

        content_label = QLabel(content, bubble)
        content_label.setObjectName("messageContent")
        content_label.setWordWrap(True)
        content_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        bubble_layout.addWidget(content_label)

        if role == "user":
            outer.addStretch(1)
            outer.addWidget(bubble, 0, Qt.AlignmentFlag.AlignRight)
        else:
            outer.addWidget(bubble, 0, Qt.AlignmentFlag.AlignLeft)
            outer.addStretch(1)


class ConversationScrollArea(QScrollArea):
    """Scrollable message bubbles with a stable testable plain-text surface."""

    delete_requested = Signal(str, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create the scroll container and its vertically stacked bubble host."""

        super().__init__(parent)
        self.setObjectName("conversationView")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._content = QWidget(self)
        self._content.setObjectName("conversationContent")
        self._layout = QVBoxLayout(self._content)
        self._layout.setContentsMargins(8, 10, 8, 10)
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
            self._layout.insertWidget(self._layout.count() - 1, bubble)
            self.bubbles.append(bubble)
            self._plain_messages.append(content)
        scrollbar = self.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def toPlainText(self) -> str:
        """Return visible message text for accessibility and regression tests."""

        return "\n".join(self._plain_messages)

    def _clear_bubbles(self) -> None:
        """Remove old bubble widgets before rendering another conversation."""

        for bubble in self.bubbles:
            self._layout.removeWidget(bubble)
            bubble.deleteLater()
        self.bubbles.clear()
