"""ChatGPT-inspired persistable light and dark QSS themes."""

from __future__ import annotations


LIGHT_COLORS: dict[str, str] = {
    "background": "#f7f7f8",
    "code_background": "#ffffff",
    "panel": "#ffffff",
    "text": "#202123",
    "muted": "#6e6e80",
    "success": "#10a37f",
    "error": "#d00e17",
    "accent": "#10a37f",
    "purple": "#7c3aed",
    "warning": "#b26a00",
    "border": "#d9d9e3",
    "hover": "#ececf1",
    "pressed": "#d2d2d9",
    "button_text": "#ffffff",
    "user_bubble": "#e9f6f1",
    "user_bubble_text": "#163d34",
    "assistant_bubble": "#f1f1f4",
    "system_bubble": "#fff7e8",
    "thinking_background": "#f0f0f0",
}

DARK_COLORS: dict[str, str] = {
    "background": "#343541",
    "code_background": "#202123",
    "panel": "#2d2d3a",
    "text": "#ececf1",
    "muted": "#acacbe",
    "success": "#19c37d",
    "error": "#ff6b6b",
    "accent": "#19c37d",
    "purple": "#c4b5fd",
    "warning": "#f5c26b",
    "border": "#565869",
    "hover": "#40414f",
    "pressed": "#4d4d5c",
    "button_text": "#ffffff",
    "user_bubble": "#244c43",
    "user_bubble_text": "#e7fff7",
    "assistant_bubble": "#2b2c38",
    "system_bubble": "#443a29",
    "thinking_background": "#40414f",
}


def _build_theme(colors: dict[str, str]) -> str:
    """Generate a complete native-widget theme from semantic colors."""

    return f"""
QWidget {{
    background-color: {colors['background']};
    color: {colors['text']};
    font-family: "Segoe UI", "Inter", "San Francisco", sans-serif;
    font-size: 14px;
}}
QMainWindow, QDialog {{ background-color: {colors['background']}; }}
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
    background-color: {colors['accent']};
    color: {colors['button_text']};
    border: 0;
    font-weight: 600;
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
QFrame#assistantBubble QLabel, QFrame#systemBubble QLabel {{ color: {colors['text']}; }}
QFrame#userBubble QLabel, QFrame#assistantBubble QLabel,
QFrame#systemBubble QLabel {{ background: transparent; border: 0; }}
QLabel#messageRoleLabel {{
    font-size: 11px;
    font-weight: 700;
}}
QLabel#messageContent {{ font-size: 14px; }}
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
QWidget#logPanel {{
    background-color: {colors['panel']};
    border: 1px solid {colors['border']};
    border-radius: 10px;
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
