"""Catppuccin Mocha theme used by the desktop interface."""

from __future__ import annotations


DARK_THEME: str = """
QWidget {
    background-color: #1e1e2e;
    color: #cdd6f4;
    font-size: 13px;
}

QMainWindow, QDialog {
    background-color: #1e1e2e;
}

QMenuBar {
    background-color: #181825;
    border-bottom: 1px solid #313244;
    padding: 3px;
}

QMenuBar::item {
    background: transparent;
    padding: 5px 10px;
    border-radius: 4px;
}

QMenuBar::item:selected, QMenuBar::item:pressed {
    background-color: #45475a;
}

QMenu {
    background-color: #181825;
    border: 1px solid #313244;
    padding: 5px;
}

QMenu::item {
    padding: 7px 28px 7px 12px;
    border-radius: 4px;
}

QMenu::item:selected {
    background-color: #45475a;
}

QMenu::separator {
    height: 1px;
    background-color: #313244;
    margin: 5px 8px;
}

QToolBar {
    background-color: #181825;
    border: 0;
    border-bottom: 1px solid #313244;
    spacing: 8px;
    padding: 5px 8px;
}

QToolBar::separator {
    background-color: #313244;
    width: 1px;
    margin: 4px 7px;
}

QLabel#modeLabel {
    color: #cba6f7;
    font-weight: 600;
}

QPushButton, QToolButton {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 5px;
    padding: 6px 12px;
}

QPushButton:hover, QToolButton:hover {
    background-color: #45475a;
    border-color: #89b4fa;
}

QPushButton:pressed, QToolButton:pressed {
    background-color: #585b70;
}

QPushButton:disabled, QToolButton:disabled {
    color: #6c7086;
    background-color: #181825;
    border-color: #313244;
}

QPushButton#sendButton, QToolButton#runButton {
    background-color: #89b4fa;
    color: #11111b;
    font-weight: 600;
}

QPushButton#applyButton {
    background-color: #a6e3a1;
    color: #11111b;
    font-weight: 600;
}

QPushButton#rejectButton {
    background-color: #f38ba8;
    color: #11111b;
    font-weight: 600;
}

QLineEdit, QTextEdit {
    background-color: #11111b;
    color: #cdd6f4;
    border: 1px solid #313244;
    border-radius: 5px;
    selection-background-color: #45475a;
    selection-color: #cdd6f4;
}

QLineEdit {
    background-color: #181825;
    padding: 7px 9px;
}

QLineEdit:focus, QTextEdit:focus {
    border-color: #89b4fa;
}

QTextEdit#logView {
    background-color: #181825;
}

QTextEdit#codeView {
    background-color: #11111b;
}

QTextEdit#codeView:focus {
    border-color: #f9e2af;
}

QSplitter::handle {
    background-color: #313244;
    width: 4px;
    height: 4px;
}

QSplitter::handle:hover {
    background-color: #89b4fa;
}

QStatusBar {
    background-color: #181825;
    color: #6c7086;
    border-top: 1px solid #313244;
}

QStatusBar::item {
    border: 0;
}

QScrollBar:vertical {
    background-color: #181825;
    width: 12px;
    margin: 0;
}

QScrollBar::handle:vertical {
    background-color: #45475a;
    min-height: 24px;
    border-radius: 5px;
}

QScrollBar::handle:vertical:hover {
    background-color: #585b70;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    height: 0;
    background: transparent;
}

QToolTip {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    padding: 4px;
}
"""
