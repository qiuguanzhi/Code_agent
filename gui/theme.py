"""Cerebro Cyber Cortex persistable light and dark QSS themes."""

from __future__ import annotations


LIGHT_COLORS: dict[str, str] = {
    "background": "#f0f4f8",
    "code_background": "#ffffff",
    "panel": "#ffffff",
    "text": "#0A192F",
    "muted": "#8892B0",
    "success": "#4ADE80",
    "error": "#FF6B6B",
    "accent": "#64FFDA",
    "purple": "#5271ff",
    "warning": "#FFD700",
    "border": "#c0c8d8",
    "hover": "#e3ebf2",
    "pressed": "#d5e0ea",
    "button_text": "#ffffff",
    "user_bubble": "#dff8f2",
    "user_bubble_text": "#0A192F",
    "assistant_bubble": "#ffffff",
    "system_bubble": "#fff9d9",
    "thinking_background": "#e7edf3",
    "toolbar_card": "#e7edf3",
    "stop_background": "#e8e8e8",
    "stop_icon": "#4a4a4a",
    "stop_hover": "#d6d6d6",
}

DARK_COLORS: dict[str, str] = {
    "background": "#0A192F",
    "code_background": "#071426",
    "panel": "#112240",
    "text": "#E6F1FF",
    "muted": "#8892B0",
    "success": "#4ADE80",
    "error": "#FF6B6B",
    "accent": "#64FFDA",
    "purple": "#a78bfa",
    "warning": "#FFD700",
    "border": "#233554",
    "hover": "#17345b",
    "pressed": "#1d426f",
    "button_text": "#ffffff",
    "user_bubble": "#123f4a",
    "user_bubble_text": "#E6F1FF",
    "assistant_bubble": "#112240",
    "system_bubble": "#3c3520",
    "thinking_background": "#0d203b",
    "toolbar_card": "#0d203b",
    "stop_background": "#3d3d3d",
    "stop_icon": "#c8c8c8",
    "stop_hover": "#505050",
}


def _build_theme(colors: dict[str, str]) -> str:
    """Generate a complete native-widget theme from semantic colors."""

    return f"""
QWidget {{
    background-color: {colors['background']};
    color: {colors['text']};
    font-family: "JetBrains Mono", "Consolas", "Courier New", "Microsoft YaHei UI", "Noto Sans CJK SC", monospace;
    font-size: 14px;
}}
QMainWindow, QDialog {{ background-color: {colors['background']}; }}
QWidget#cerebroBackground {{ background-color: {colors['background']}; }}
QWidget#workspacePanel, QWidget#conversationPanel, QWidget#codePanel {{
    background: transparent;
}}
QWidget#pulseIndicator, QWidget#alphaWaveIndicator {{
    background: transparent;
    border: 0;
}}
QMenuBar, QToolBar, QStatusBar {{
    background-color: {colors['panel']};
    border-color: {colors['border']};
    font-size: 12px;
}}
QMenuBar {{ border-bottom: 1px solid {colors['border']}; padding: 4px 8px; }}
QMenuBar::item {{ padding: 6px 11px; border-radius: 6px; }}
QMenuBar::item:selected, QMenuBar::item:pressed {{ background-color: {colors['hover']}; }}
QMenu {{
    background-color: {colors['panel']};
    border: 1px solid {colors['border']};
    border-radius: 8px;
    padding: 6px;
}}
QMenu::item {{ padding: 8px 30px 8px 13px; border-radius: 6px; }}
QMenu::item:selected {{ background-color: {colors['hover']}; }}
QMenu::separator {{ height: 1px; background: {colors['border']}; margin: 6px 8px; }}
QToolBar {{ border-bottom: 1px solid {colors['border']}; spacing: 9px; padding: 7px 10px; }}
QToolBar::separator {{ background: {colors['border']}; width: 1px; margin: 4px 7px; }}
QLabel#snapshotLabel, QLabel#waitingIndicator {{ color: {colors['warning']}; }}
QLabel#statusIndicator, QLabel#workspaceLabel, QLabel#snapshotLabel {{
    background-color: {colors['toolbar_card']};
    border: 1px solid {colors['border']};
    border-radius: 8px;
    padding: 5px 9px;
}}
QLabel#workspaceEmptyLabel {{ color: {colors['muted']}; font-size: 12px; }}
QPushButton, QToolButton {{
    background-color: {colors['panel']};
    color: {colors['text']};
    border: 1px solid {colors['border']};
    border-radius: 8px;
    padding: 7px 13px;
}}
QPushButton:hover, QToolButton:hover {{
    background-color: {colors['hover']};
    border-color: {colors['accent']};
}}
QPushButton:pressed, QToolButton:pressed {{ background-color: {colors['pressed']}; }}
QPushButton:disabled, QToolButton:disabled {{
    color: {colors['muted']};
    background-color: {colors['panel']};
    border-color: {colors['border']};
}}
QPushButton#sendButton {{
    background-color: #1a7f5c;
    color: #ffffff;
    border: 0;
    font-weight: 600;
}}
QPushButton#sendButton:hover {{ background-color: #156a4d; }}
QPushButton#sendButton:pressed {{ background-color: #238e69; }}
QPushButton#sendButton:disabled {{
    background-color: #888888;
    color: #eeeeee;
}}
QPushButton#sendButton[stopMode="true"] {{
    background-color: {colors['stop_background']};
    border-radius: 18px;
    min-width: 36px;
    max-width: 36px;
    min-height: 36px;
    max-height: 36px;
    padding: 0;
}}
QPushButton#sendButton[stopMode="true"]:hover {{
    background-color: {colors['stop_hover']};
    border: 0;
}}
QToolButton#thinkingFoldButton, QToolButton#logFoldButton {{
    background: transparent;
    border: 0;
    color: {colors['accent']};
    padding: 2px 5px;
    font-size: 13px;
}}
QToolButton#thinkingFoldButton:hover, QToolButton#logFoldButton:hover {{
    background-color: {colors['hover']};
}}
QPushButton#manualSaveButton {{
    background: transparent;
    color: {colors['muted']};
    border-color: {colors['border']};
    padding: 4px 10px;
    font-size: 12px;
}}
QPushButton#applyButton {{
    background-color: {colors['success']};
    color: {colors['button_text']};
    border: 0;
    font-weight: 700;
    padding: 8px 18px;
}}
QPushButton#rejectButton {{
    background-color: {colors['error']};
    color: {colors['button_text']};
    border: 0;
    font-weight: 700;
    padding: 8px 18px;
}}
QPushButton#thinkingToggle {{
    background: transparent;
    border: 0;
    color: {colors['muted']};
    text-align: left;
    font-size: 12px;
    font-weight: 600;
    padding: 3px 5px;
}}
QPushButton#toolStatusButton {{
    background-color: {colors['panel']};
    border: 1px solid {colors['border']};
    border-radius: 7px;
    padding: 4px 10px;
    font-size: 12px;
}}
QLineEdit, QTextEdit, QPlainTextEdit, QTextBrowser, QListWidget, QTreeView, QComboBox {{
    background-color: {colors['code_background']};
    color: {colors['text']};
    border: 1px solid {colors['border']};
    border-radius: 10px;
    selection-background-color: {colors['hover']};
    selection-color: {colors['text']};
}}
QLineEdit, QComboBox {{ background-color: {colors['panel']}; padding: 9px 11px; }}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QListWidget:focus, QTreeView:focus {{
    border-color: {colors['accent']};
}}
QScrollArea#conversationView, QWidget#conversationContent {{
    background-color: {colors['background']};
    border: 0;
}}
QWidget#conversationContent {{ padding: 0; }}
QScrollArea#conversationView > QWidget > QWidget {{
    background-color: {colors['background']};
}}
QFrame#userBubble, QFrame#assistantBubble, QFrame#systemBubble {{
    border: 1px solid {colors['border']};
    border-radius: 12px;
}}
QFrame#userBubble {{
    background-color: {colors['user_bubble']};
    border-color: {colors['accent']};
}}
QFrame#assistantBubble {{
    background-color: {colors['assistant_bubble']};
}}
QFrame#systemBubble {{
    background-color: {colors['system_bubble']};
    border-color: {colors['warning']};
}}
QFrame#userBubble QLabel {{ color: {colors['user_bubble_text']}; }}
QFrame#assistantBubble QLabel, QFrame#assistantBubble QTextBrowser,
QFrame#systemBubble QLabel {{ color: {colors['text']}; }}
QFrame#userBubble QLabel, QFrame#assistantBubble QLabel,
QFrame#assistantBubble QTextBrowser, QFrame#systemBubble QLabel {{
    background: transparent;
    border: 0;
}}
QLabel#messageRoleLabel {{
    font-size: 11px;
    font-weight: 700;
}}
QLabel#messageContent, QTextBrowser#messageContent {{
    font-size: 14px;
    background: transparent;
    border: 0;
    padding: 0;
}}
QToolButton#messageDeleteButton {{
    background: transparent;
    border: 0;
    border-radius: 6px;
    color: {colors['muted']};
    padding: 1px 5px;
    font-size: 16px;
}}
QToolButton#messageDeleteButton:hover {{
    background-color: {colors['hover']};
    color: {colors['error']};
}}
QMenu#fileMentionPopup {{
    background-color: {colors['panel']};
    border: 1px solid {colors['border']};
    border-radius: 8px;
}}
QListWidget#fileMentionList {{
    background-color: {colors['panel']};
    border: 0;
    border-radius: 6px;
    padding: 3px;
}}
QListWidget#fileMentionList::item {{ padding: 6px 9px; border-radius: 5px; }}
QListWidget#fileMentionList::item:selected {{
    background-color: {colors['hover']};
    color: {colors['text']};
}}

QFrame#batchDiffPanel {{
    background-color: {colors['toolbar_card']};
    border: 1px solid {colors['accent']};
    border-radius: 8px;
}}

QLabel#batchDiffSummary {{
    color: {colors['warning']};
    font-weight: 700;
}}

QListWidget#batchDiffList {{
    background-color: {colors['code_background']};
    border: 1px solid {colors['border']};
    border-radius: 6px;
}}

QListWidget#batchDiffList::item {{ padding: 6px 8px; }}
QListWidget#batchDiffList::item:selected {{
    color: {colors['text']};
    background-color: {colors['hover']};
}}
QWidget#logPanel {{
    background-color: {colors['panel']};
    border: 1px solid {colors['border']};
    border-radius: 10px;
}}
QLabel#panelTitle, QLabel#thinkingTitle {{
    color: {colors['muted']};
    background: transparent;
    border: 0;
    font-size: 12px;
    font-weight: 700;
}}
QWidget#thinkingContainer {{
    background-color: {colors['thinking_background']};
    border: 1px solid {colors['border']};
    border-radius: 10px;
}}
QTextBrowser#thinkingView {{
    background-color: {colors['thinking_background']};
    color: {colors['muted']};
    border: 0;
    padding: 5px 12px;
    font-size: 12px;
}}
QTextEdit#logView {{ background-color: {colors['panel']}; border: 0; font-size: 12px; }}
QTextEdit#codeView {{ background-color: {colors['code_background']}; }}
QTreeView {{ background-color: {colors['code_background']}; alternate-background-color: {colors['panel']}; }}
QTreeView::item {{ padding: 5px; border-radius: 4px; }}
QTreeView::item:selected {{ background-color: {colors['hover']}; color: {colors['text']}; }}
QProgressBar {{
    background-color: {colors['panel']};
    border: 1px solid {colors['border']};
    border-radius: 5px;
    min-height: 9px;
    max-height: 9px;
}}
QProgressBar::chunk {{ background-color: {colors['accent']}; border-radius: 4px; }}
QLabel#loadingLabel {{ color: {colors['accent']}; font-weight: 700; }}
QTabWidget::pane {{
    border: 1px solid {colors['border']};
    border-radius: 8px;
    background: {colors['code_background']};
}}
QTabBar::tab {{
    background: {colors['panel']};
    color: {colors['muted']};
    border: 1px solid {colors['border']};
    border-bottom: 0;
    padding: 7px 14px;
}}
QTabBar::tab:selected {{ color: {colors['text']}; background: {colors['hover']}; }}
QStackedWidget#codeStack, QWidget#codeEmptyPage, QLabel#codeEmptyLabel {{
    background-color: {colors['code_background']};
    color: {colors['muted']};
    border: 0;
    border-radius: 0;
}}
QLabel#codeEmptyLabel {{ font-size: 13px; }}
QLabel#manualFileStatus {{
    background: transparent;
    color: {colors['muted']};
    font-size: 12px;
}}
QSplitter::handle {{ background-color: {colors['border']}; width: 4px; height: 4px; }}
QSplitter::handle:hover {{ background-color: {colors['accent']}; }}
QStatusBar {{ color: {colors['muted']}; border-top: 1px solid {colors['border']}; }}
QStatusBar::item {{ border: 0; }}
QScrollBar:vertical {{ background: transparent; width: 6px; margin: 2px 0; }}
QScrollBar::handle:vertical {{
    background: {colors['muted']};
    min-height: 28px;
    border-radius: 3px;
}}
QScrollBar::handle:vertical:hover {{ background: {colors['accent']}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ height: 0; background: transparent; }}
QToolTip {{
    background: {colors['panel']};
    color: {colors['text']};
    border: 1px solid {colors['border']};
    border-radius: 5px;
    padding: 5px;
}}
"""


LIGHT_THEME: str = _build_theme(LIGHT_COLORS)
DARK_THEME: str = _build_theme(DARK_COLORS)


def get_theme(theme_name: str) -> tuple[str, dict[str, str]]:
    """Return a stylesheet and semantic palette for a persisted theme name."""

    if theme_name == "light":
        return LIGHT_THEME, LIGHT_COLORS
    return DARK_THEME, DARK_COLORS
