"""Native reusable widgets for the desktop conversation interface."""

from __future__ import annotations

import math
import re
from html import escape
from typing import Any

from PySide6.QtCore import QPoint, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QHideEvent,
    QResizeEvent,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QScrollArea,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)


class _CerebroOverlay(QWidget):
    """Mouse-transparent foreground used to keep the watermark visible."""

    def __init__(self, owner: CerebroBackground) -> None:
        """Bind the overlay to its central background owner."""

        super().__init__(owner)
        self._owner = owner
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def paintEvent(self, event: object) -> None:
        """Delegate vector painting without taking input focus."""

        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._owner._paint_overlay(painter)


class CerebroBackground(QWidget):
    """Lightweight animated neural watermark and active energy scan."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create the central canvas with one shared 20 FPS timer."""

        super().__init__(parent)
        self.setObjectName("cerebroBackground")
        self._dark = True
        self._active = False
        self._angle = 0.0
        self._scan_offset = -0.35
        self._animation_timer = QTimer(self)
        self._animation_timer.setInterval(50)
        self._animation_timer.timeout.connect(self._advance_animation)
        self._overlay = _CerebroOverlay(self)

    def set_theme(self, theme_name: str) -> None:
        """Select the low-opacity watermark palette and repaint."""

        self._dark = theme_name != "light"
        self._overlay.update()

    def set_active(self, active: bool) -> None:
        """Enable or pause the brighter task-only energy sweep."""

        self._active = active
        if not active:
            self._scan_offset = -0.35
        self._overlay.update()

    def stop_animation(self) -> None:
        """Stop repaint scheduling when the owning main window closes."""

        self._active = False
        self._animation_timer.stop()

    def showEvent(self, event: QShowEvent) -> None:
        """Animate only while the canvas is actually visible."""

        super().showEvent(event)
        self._overlay.setGeometry(self.rect())
        self._overlay.raise_()
        self._animation_timer.start()

    def hideEvent(self, event: QHideEvent) -> None:
        """Release the 20 FPS timer whenever the window is hidden."""

        self._animation_timer.stop()
        super().hideEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Keep the mouse-transparent visual layer over every central panel."""

        super().resizeEvent(event)
        self._overlay.setGeometry(self.rect())
        self._overlay.raise_()

    def _advance_animation(self) -> None:
        """Rotate the network slowly and advance the active gradient."""

        self._angle = (self._angle + 0.1) % 360.0
        if self._active:
            # 1.70 normalized widths at 20 FPS: one sweep takes about 9.4 s.
            self._scan_offset += 0.009
            if self._scan_offset > 1.35:
                self._scan_offset = -0.35
        self._overlay.update()

    def _paint_overlay(self, painter: QPainter) -> None:
        """Paint a sparse deterministic neural mesh over opaque child panels."""

        width = max(1, self.width())
        height = max(1, self.height())
        network_color = QColor("#64FFDA" if self._dark else "#c0c8d8")
        network_color.setAlphaF(0.065 if self._dark else 0.08)
        painter.save()
        painter.translate(width / 2, height / 2)
        painter.rotate(self._angle)
        painter.translate(-width / 2, -height / 2)
        painter.setPen(QPen(network_color, 1.0))
        nodes = [
            (0.08, 0.24), (0.18, 0.72), (0.31, 0.38), (0.43, 0.81),
            (0.56, 0.18), (0.65, 0.62), (0.79, 0.32), (0.91, 0.74),
        ]
        for index, (x_ratio, y_ratio) in enumerate(nodes):
            x = x_ratio * width
            y = y_ratio * height
            painter.drawEllipse(QRectF(x - 2, y - 2, 4, 4))
            for neighbor in (index + 1, index + 3):
                if neighbor >= len(nodes):
                    continue
                nx, ny = nodes[neighbor]
                path = QPainterPath()
                path.moveTo(x, y)
                path.cubicTo(
                    (x + nx * width) / 2,
                    y - height * 0.06,
                    (x + nx * width) / 2,
                    ny * height + height * 0.06,
                    nx * width,
                    ny * height,
                )
                painter.drawPath(path)
        painter.restore()

        if self._active:
            gradient = QLinearGradient(0, 0, width, height)
            center = min(1.0, max(0.0, self._scan_offset))
            transparent = QColor("#64FFDA")
            transparent.setAlpha(0)
            glow = QColor("#64FFDA")
            glow.setAlpha(31)
            gradient.setColorAt(max(0.0, center - 0.04), transparent)
            gradient.setColorAt(center, glow)
            gradient.setColorAt(min(1.0, center + 0.04), transparent)
            painter.fillRect(self.rect(), QBrush(gradient))


class PulseIndicator(QWidget):
    """Small status dot with a timer-driven opacity pulse."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create a compact indicator whose color follows semantic state."""

        super().__init__(parent)
        self.setObjectName("pulseIndicator")
        self.setFixedSize(14, 14)
        self._color = QColor("#4ADE80")
        self._direction = -0.08
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(1.0)
        self.setGraphicsEffect(self._effect)
        self._timer = QTimer(self)
        self._timer.setInterval(70)
        self._timer.timeout.connect(self._pulse)

    def set_state(self, state: str) -> None:
        """Update color and animate only while Agent work is active."""

        colors = {"ready": "#4ADE80", "running": "#FFD700", "error": "#FF6B6B"}
        self._color = QColor(colors.get(state, colors["error"]))
        if state == "running":
            self._timer.start()
        else:
            self._timer.stop()
            self._effect.setOpacity(1.0)
        self.update()

    def _pulse(self) -> None:
        """Oscillate opacity between readable limits."""

        opacity = self._effect.opacity() + self._direction
        if opacity <= 0.38 or opacity >= 1.0:
            self._direction *= -1
            opacity = min(1.0, max(0.38, opacity))
        self._effect.setOpacity(opacity)

    def paintEvent(self, event: object) -> None:
        """Paint a soft outer glow and solid energy dot."""

        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        glow = QColor(self._color)
        glow.setAlpha(70)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(QRectF(0, 0, 14, 14))
        painter.setBrush(self._color)
        painter.drawEllipse(QRectF(3, 3, 8, 8))


class BrainWaveIndicator(QWidget):
    """Compact alpha-wave display animated only during Agent activity."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create an idle waveform with an inexpensive timer."""

        super().__init__(parent)
        self.setObjectName("alphaWaveIndicator")
        self.setFixedSize(118, 28)
        self._active = False
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(90)
        self._timer.timeout.connect(self._advance)

    def set_active(self, active: bool) -> None:
        """Start an 8-12 Hz-inspired visual pulse or return to idle."""

        self._active = active
        if active:
            self._timer.start()
        else:
            self._timer.stop()
            self._phase = 0.0
        self.update()

    def _advance(self) -> None:
        """Move the wave phase without performing work in the main slots."""

        self._phase = (self._phase + 0.72) % (2 * math.pi)
        self.update()

    def paintEvent(self, event: object) -> None:
        """Draw the alpha label and a smooth electroencephalogram line."""

        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        color = QColor("#64FFDA" if self._active else "#8892B0")
        painter.setPen(QPen(color, 1.4))
        painter.drawText(QRectF(0, 0, 28, 28), Qt.AlignmentFlag.AlignCenter, "α")
        path = QPainterPath()
        baseline = 14.0
        path.moveTo(28, baseline)
        for x in range(29, 116, 2):
            amplitude = 6.0 if self._active else 2.0
            y = baseline + math.sin((x - 28) * 0.20 + self._phase) * amplitude
            path.lineTo(x, y)
        painter.drawPath(path)


def basic_markdown_to_html(markdown_text: str) -> str:
    """Render a small, escaped Markdown subset suitable for Agent answers."""

    lines = markdown_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    output: list[str] = []
    list_items: list[str] = []
    code_lines: list[str] = []
    in_code = False

    def render_inline(value: str) -> str:
        """Escape untrusted text, then render inline code and strong spans."""

        safe = escape(value)
        safe = re.sub(r"`([^`]+)`", r"<code>\1</code>", safe)
        return re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", safe)

    def flush_list() -> None:
        """Append a pending unordered list as one semantic block."""

        if not list_items:
            return
        output.append(
            "<ul style='margin:4px 0 6px 18px'>"
            + "".join(f"<li>{item}</li>" for item in list_items)
            + "</ul>"
        )
        list_items.clear()

    for line in lines:
        if line.strip().startswith("```"):
            flush_list()
            if in_code:
                output.append(
                    "<pre style='background-color:rgba(127,127,127,0.14);"
                    "padding:8px;border-radius:6px'><code>"
                    + "\n".join(code_lines)
                    + "</code></pre>"
                )
                code_lines.clear()
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            code_lines.append(escape(line))
            continue

        heading = re.match(r"^\s*#{1,4}\s+(.+)$", line)
        item = re.match(r"^\s*-\s+(.+)$", line)
        quote = re.match(r"^\s*>\s?(.*)$", line)
        if item is not None:
            list_items.append(render_inline(item.group(1)))
            continue
        flush_list()
        if heading is not None:
            level = min(4, max(1, len(line) - len(line.lstrip("#"))))
            output.append(f"<h{level}>{render_inline(heading.group(1))}</h{level}>")
        elif quote is not None:
            output.append(
                "<blockquote style='background-color:rgba(127,127,127,0.14);"
                "border-left:3px solid #6c7086;padding:6px 10px;margin:4px 0'>"
                f"{render_inline(quote.group(1))}</blockquote>"
            )
        elif line.strip():
            output.append(f"<p>{render_inline(line.strip())}</p>")
        else:
            output.append("<br>")
    flush_list()
    if in_code:
        output.append(
            "<pre style='background-color:rgba(127,127,127,0.14);padding:8px'>"
            "<code>" + "\n".join(code_lines) + "</code></pre>"
        )
    return "".join(output)


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
            "你" if role == "user" else "🧠 Cerebro" if role == "assistant" else "系统",
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

        if role == "assistant":
            content_widget = QTextBrowser(self.bubble_frame)
            content_widget.setFrameShape(QFrame.Shape.NoFrame)
            content_widget.setOpenExternalLinks(False)
            content_widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            content_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            content_widget.setSizeAdjustPolicy(
                QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents
            )
            content_widget.setHtml(basic_markdown_to_html(content))
        else:
            content_widget = QLabel(content, self.bubble_frame)
            content_widget.setWordWrap(True)
            content_widget.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
                | Qt.TextInteractionFlag.TextSelectableByKeyboard
            )
        self.content_label = content_widget
        self.content_label.setObjectName("messageContent")
        bubble_layout.addWidget(self.content_label)

        if role == "user":
            outer.addStretch(1)
            outer.addWidget(self.bubble_frame, 0, Qt.AlignmentFlag.AlignRight)
        else:
            outer.addWidget(self.bubble_frame, 0, Qt.AlignmentFlag.AlignLeft)
            outer.addStretch(1)

    def set_content(self, content: str) -> None:
        """Update one bubble in place so streaming never rebuilds the feed."""

        self._uses_wide_layout = len(content) >= 120
        if isinstance(self.content_label, QTextBrowser):
            self.content_label.setHtml(basic_markdown_to_html(content))
        else:
            self.content_label.setText(content)
        parent_width = self.parentWidget().width() if self.parentWidget() else 600
        self.set_width_constraints(max(80, int(parent_width * 0.85)))

    def set_width_constraints(self, width: int) -> None:
        """Apply a responsive 85%-parent cap with a readable normal minimum."""

        safe_width = max(80, width)
        minimum = 300
        if self._uses_wide_layout:
            minimum = max(minimum, math.ceil(safe_width * (0.80 / 0.85)))
        self.bubble_frame.setMinimumWidth(min(minimum, safe_width))
        self.bubble_frame.setMaximumWidth(safe_width)
        if isinstance(self.content_label, QTextBrowser):
            content_width = max(
                40,
                safe_width - 26 if self._uses_wide_layout else min(520, safe_width - 26),
            )
            self.content_label.setFixedWidth(content_width)
            document = self.content_label.document()
            document.setTextWidth(max(40, content_width - 16))
            self.content_label.setFixedHeight(
                max(34, math.ceil(document.size().height()) + 8)
            )
        elif self._uses_wide_layout:
            self.content_label.setFixedWidth(max(40, safe_width - 26))
            self.content_label.adjustSize()
        if self._uses_wide_layout or isinstance(self.content_label, QTextBrowser):
            self.bubble_frame.layout().activate()
            self.bubble_frame.adjustSize()


class ConversationScrollArea(QScrollArea):
    """Scrollable message bubbles with a stable testable plain-text surface."""

    delete_requested = Signal(str, int)
    BUBBLE_WIDTH_RATIO = 0.85
    MAX_RENDERED_MESSAGES = 200

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
        self._conversation_id = ""
        self._render_start_index = 0

    def set_messages(
        self,
        conversation_id: str,
        messages: list[dict[str, Any]],
    ) -> None:
        """Replace visible bubbles while preserving only trusted text fields."""

        self.setUpdatesEnabled(False)
        try:
            self._clear_bubbles()
            self._plain_messages = []
            start_index = max(0, len(messages) - self.MAX_RENDERED_MESSAGES)
            self._conversation_id = conversation_id
            self._render_start_index = start_index
            for index in range(start_index, len(messages)):
                message = messages[index]
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
        finally:
            self.setUpdatesEnabled(True)
            self.viewport().update()
        scrollbar = self.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def update_message_content(
        self,
        conversation_id: str,
        message_index: int,
        content: str,
    ) -> bool:
        """Update a rendered message directly and keep its plain mirror aligned."""

        if conversation_id != self._conversation_id:
            return False
        visible_index = message_index - self._render_start_index
        if not 0 <= visible_index < len(self.bubbles):
            return False
        self.bubbles[visible_index].set_content(content)
        if visible_index < len(self._plain_messages):
            self._plain_messages[visible_index] = content
        scrollbar = self.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        return True

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


class BatchDiffWidget(QFrame):
    """Compact selector for one batch of staged file modifications."""

    file_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create a summary label and a clickable per-file Diff list."""

        super().__init__(parent)
        self.setObjectName("batchDiffPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)
        self.summary_label = QLabel("待审批修改", self)
        self.summary_label.setObjectName("batchDiffSummary")
        layout.addWidget(self.summary_label)
        self.file_list = QListWidget(self)
        self.file_list.setObjectName("batchDiffList")
        self.file_list.setMaximumHeight(150)
        self.file_list.itemClicked.connect(self._emit_selected)
        self.file_list.itemActivated.connect(self._emit_selected)
        layout.addWidget(self.file_list)
        self.setVisible(False)

    def set_pending_writes(self, pending_writes: list[dict[str, Any]]) -> None:
        """Replace the batch summary with paths and line-change statistics."""

        self.file_list.clear()
        for entry in pending_writes:
            path = str(entry.get("path", ""))
            diff_text = str(entry.get("diff", ""))
            additions, deletions = self._count_diff(diff_text)
            item = QListWidgetItem(f"{path}  (+{additions} -{deletions})")
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.file_list.addItem(item)
        count = self.file_list.count()
        self.summary_label.setText(f"待审批修改 · {count} 个文件（点击查看 Diff）")
        if count:
            self.file_list.setCurrentRow(0)
        self.setVisible(count > 0)

    def clear_batch(self) -> None:
        """Clear all staged-file rows and hide the panel."""

        self.file_list.clear()
        self.setVisible(False)

    def _emit_selected(self, item: QListWidgetItem) -> None:
        """Publish the trusted path stored on one list item."""

        path = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(path, str) and path:
            self.file_selected.emit(path)

    @staticmethod
    def _count_diff(diff_text: str) -> tuple[int, int]:
        """Count changed content lines while excluding Unified Diff headers."""

        additions = 0
        deletions = 0
        for line in diff_text.splitlines():
            if line.startswith("+++") or line.startswith("---"):
                continue
            if line.startswith("+"):
                additions += 1
            elif line.startswith("-"):
                deletions += 1
        return additions, deletions


class FileMentionPopup(QMenu):
    """Popup list used to insert fuzzy-matched workspace file mentions."""

    mention_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create an initially hidden, keyboard-friendly file list."""

        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Popup)
        self.setObjectName("fileMentionPopup")
        self._host = QWidget(self)
        layout = QVBoxLayout(self._host)
        layout.setContentsMargins(4, 4, 4, 4)
        self.list_widget = QListWidget(self._host)
        self.list_widget.setObjectName("fileMentionList")
        self.list_widget.itemClicked.connect(self._select_item)
        self.list_widget.itemActivated.connect(self._select_item)
        layout.addWidget(self.list_widget)
        self._widget_action = QWidgetAction(self)
        self._widget_action.setDefaultWidget(self._host)
        self.addAction(self._widget_action)
        self._all_paths: list[str] = []
        self.hide()

    def set_files(self, paths: list[str]) -> None:
        """Replace the available ordinary workspace-file paths."""

        self._all_paths = sorted(dict.fromkeys(paths), key=str.casefold)

    def show_matches(self, anchor: QLineEdit, query: str) -> None:
        """Filter paths fuzzily and show above the input, falling back below."""

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
        self._host.setFixedSize(width, height)
        self.adjustSize()
        popup_height = max(height, self.sizeHint().height())
        screen = anchor.screen().availableGeometry()
        anchor_top = anchor.mapToGlobal(QPoint(0, 0))
        above_y = anchor_top.y() - popup_height - 4
        below_y = anchor_top.y() + anchor.height() + 4
        y = above_y if above_y >= screen.top() else below_y
        x = min(max(screen.left(), anchor_top.x()), screen.right() - width + 1)
        self.popup(QPoint(x, y))

    def dispose(self) -> None:
        """Close the native popup handle before its owning window is hidden."""

        self.close()

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
