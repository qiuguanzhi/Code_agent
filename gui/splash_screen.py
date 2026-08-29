"""Cerebro branded non-blocking startup sequence."""

from __future__ import annotations

import math
import time

from PySide6.QtCore import (
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QTimer,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPaintEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QWidget


class SplashScreen(QWidget):
    """Paint the 2.5-second Cerebro boot sequence without blocking the event loop."""

    finished = Signal()
    DURATION_MS = 2_500

    def __init__(self, parent: QWidget | None = None) -> None:
        """Prepare all animation state while keeping construction side-effect free."""

        super().__init__(parent, Qt.WindowType.FramelessWindowHint | Qt.WindowType.SplashScreen)
        self.setObjectName("cerebroSplash")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setFixedSize(620, 420)
        self._elapsed_ms = 0
        self._started_at = 0.0
        self._completed = False
        self._fade_started = False
        self._timer = QTimer(self)
        self._timer.setInterval(32)
        self._timer.timeout.connect(self._tick)
        self._fade_animation: QPropertyAnimation | None = None

    def start(self) -> None:
        """Center, show, and start the three-stage non-blocking animation."""

        try:
            screen = QApplication.primaryScreen()
            if screen is not None:
                geometry = screen.availableGeometry()
                self.move(geometry.center() - self.rect().center())
            self.setWindowOpacity(1.0)
            self._started_at = time.perf_counter()
            self.show()
            self.raise_()
            self._timer.start()
        except Exception:
            self._complete()

    def skip(self) -> None:
        """Skip immediately, as used by mouse input and automated smoke tests."""

        self._complete()

    def _tick(self) -> None:
        """Update elapsed time and begin the final fade at 2.25 seconds."""

        try:
            self._elapsed_ms = int((time.perf_counter() - self._started_at) * 1_000)
            self.update()
            if self._elapsed_ms >= 2_250 and not self._fade_started:
                self._begin_fade()
            if self._elapsed_ms >= self.DURATION_MS + 100:
                self._complete()
        except Exception:
            self._complete()

    def _begin_fade(self) -> None:
        """Fade the splash while the last logo characters settle."""

        self._fade_started = True
        animation = QPropertyAnimation(self, b"windowOpacity", self)
        animation.setDuration(250)
        animation.setStartValue(1.0)
        animation.setEndValue(0.0)
        animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        animation.finished.connect(self._complete)
        self._fade_animation = animation
        animation.start()

    def _complete(self) -> None:
        """Finish exactly once and allow the owner to reveal its main window."""

        if self._completed:
            return
        self._completed = True
        self._timer.stop()
        self.finished.emit()
        self.close()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Allow any pointer click to bypass the startup sequence."""

        event.accept()
        self.skip()

    def paintEvent(self, event: QPaintEvent) -> None:
        """Render the current boot stage using only vector primitives."""

        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        panel = QColor("#0A192F")
        panel.setAlpha(245)
        painter.setBrush(panel)
        painter.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2), 24, 24)
        painter.setPen(QPen(QColor("#233554"), 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2), 24, 24)

        elapsed = self._elapsed_ms
        brain_opacity = 1.0 if elapsed < 1_600 else max(0.0, 1.0 - (elapsed - 1_600) / 650)
        painter.save()
        painter.setOpacity(brain_opacity)
        pulse_scale = 1.0 + 0.01 * math.sin(elapsed / 55.0)
        painter.translate(310, 145)
        painter.scale(pulse_scale, pulse_scale)
        painter.translate(-310, -145)
        self._draw_brain(painter, elapsed)
        painter.restore()

        if 800 <= elapsed < 1_750:
            self._draw_terminal(painter, elapsed)
        if elapsed >= 1_600:
            self._draw_logo(painter, elapsed)

    @staticmethod
    def _draw_brain(painter: QPainter, elapsed: int) -> None:
        """Draw the holographic outline and progressively activated branches."""

        cyan = QColor("#64FFDA")
        painter.setPen(QPen(cyan, 2.0))
        outline = QPainterPath(QPointF(310, 245))
        outline.cubicTo(250, 248, 205, 205, 218, 145)
        outline.cubicTo(230, 78, 286, 66, 310, 103)
        outline.cubicTo(334, 66, 390, 78, 402, 145)
        outline.cubicTo(415, 205, 370, 248, 310, 245)
        painter.drawPath(outline)

        if elapsed >= 800:
            fill = QColor(cyan)
            fill.setAlpha(75)
            painter.setBrush(fill)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPath(outline)
            painter.setBrush(Qt.BrushStyle.NoBrush)

        growth = min(1.0, max(0.0, elapsed / 800.0))
        branches = [
            ((310, 238), (310, 180)),
            ((310, 198), (270, 158)),
            ((310, 198), (350, 158)),
            ((270, 158), (246, 120)),
            ((270, 158), (286, 112)),
            ((350, 158), (334, 112)),
            ((350, 158), (374, 120)),
        ]
        painter.setPen(QPen(cyan, 1.2))
        count = max(1, math.ceil(len(branches) * growth))
        for start, end in branches[:count]:
            painter.drawLine(QPointF(*start), QPointF(*end))
            painter.drawEllipse(QRectF(end[0] - 3, end[1] - 3, 6, 6))

    @staticmethod
    def _draw_terminal(painter: QPainter, elapsed: int) -> None:
        """Show staged Chinese terminal lines, progress, and neural nodes."""

        stage_progress = min(1.0, max(0.0, (elapsed - 800) / 800.0))
        percent = int(stage_progress * 100)
        filled = min(11, percent * 11 // 100)
        lines = [
            "> 正在连接神经链接...",
            f"> 放大认知带宽...  [{'=' * filled}{'-' * (11 - filled)}] {percent:02d}%",
            "> 加载代码库索引...",
        ]
        font = QFont("JetBrains Mono", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        painter.setFont(font)
        painter.setPen(QColor("#8892B0"))
        visible_lines = max(1, min(3, int(stage_progress * 4)))
        for index, line in enumerate(lines[:visible_lines]):
            painter.drawText(125, 292 + index * 24, line)
        for index in range(5):
            color = QColor("#FFD700" if stage_progress > (index + 1) / 6 else "#64FFDA")
            color.setAlpha(230 if stage_progress > index / 6 else 70)
            painter.setBrush(color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QRectF(264 + index * 23, 367, 8, 8))

    @staticmethod
    def _draw_logo(painter: QPainter, elapsed: int) -> None:
        """Type the final brand and slogan during stage three."""

        logo = "CEREBRO"
        logo_count = min(len(logo), max(0, (elapsed - 1_600) // 80 + 1))
        slogan = "Amplify your Code."
        slogan_count = min(len(slogan), max(0, (elapsed - 1_920) // 32 + 1))
        logo_font = QFont("JetBrains Mono", 28, QFont.Weight.Bold)
        logo_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 5)
        painter.setFont(logo_font)
        painter.setPen(QColor("#64FFDA"))
        painter.drawText(QRectF(0, 170, 620, 60), Qt.AlignmentFlag.AlignCenter, logo[:logo_count])
        subtitle_font = QFont("JetBrains Mono", 11)
        painter.setFont(subtitle_font)
        painter.setPen(QColor("#E6F1FF"))
        painter.drawText(
            QRectF(0, 232, 620, 40),
            Qt.AlignmentFlag.AlignCenter,
            slogan[:slogan_count],
        )
