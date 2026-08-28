"""Persistable light and dark QSS themes for every native GUI component."""

from __future__ import annotations


DARK_COLORS: dict[str, str] = {
    "background": "#1e1e2e",
    "code_background": "#11111b",
    "panel": "#181825",
    "text": "#cdd6f4",
    "muted": "#6c7086",
    "success": "#a6e3a1",
    "error": "#f38ba8",
    "accent": "#89b4fa",
    "purple": "#cba6f7",
    "warning": "#f9e2af",
    "border": "#313244",
    "hover": "#45475a",
    "pressed": "#585b70",
    "button_text": "#11111b",
}

LIGHT_COLORS: dict[str, str] = {
    "background": "#eff1f5",
    "code_background": "#e6e9ef",
    "panel": "#dce0e8",
    "text": "#4c4f69",
    "muted": "#8c8fa1",
    "success": "#40a02b",
    "error": "#d20f39",
    "accent": "#1e66f5",
    "purple": "#8839ef",
    "warning": "#df8e1d",
    "border": "#bcc0cc",
    "hover": "#ccd0da",
    "pressed": "#acb0be",
    "button_text": "#eff1f5",
}


def _build_theme(colors: dict[str, str]) -> str:
    """Generate one complete native-widget stylesheet from semantic colors."""

    return f"""
QWidget {{
    background-color: {colors['background']};
    color: {colors['text']};
    font-size: 13px;
}}
QMainWindow, QDialog {{ background-color: {colors['background']}; }}
QMenuBar, QToolBar, QStatusBar {{
    background-color: {colors['panel']};
    border-color: {colors['border']};
}}
QMenuBar {{ border-bottom: 1px solid {colors['border']}; padding: 3px; }}
QMenuBar::item {{ padding: 5px 10px; border-radius: 4px; }}
QMenuBar::item:selected, QMenuBar::item:pressed {{ background-color: {colors['hover']}; }}
QMenu {{
    background-color: {colors['panel']};
    border: 1px solid {colors['border']};
    padding: 5px;
}}
QMenu::item {{ padding: 7px 28px 7px 12px; border-radius: 4px; }}
QMenu::item:selected {{ background-color: {colors['hover']}; }}
QMenu::separator {{ height: 1px; background: {colors['border']}; margin: 5px 8px; }}
QToolBar {{ border-bottom: 1px solid {colors['border']}; spacing: 8px; padding: 5px 8px; }}
QToolBar::separator {{ background: {colors['border']}; width: 1px; margin: 4px 7px; }}
QLabel#modeLabel {{ color: {colors['purple']}; font-weight: 600; }}
QLabel#snapshotLabel, QLabel#waitingIndicator {{ color: {colors['warning']}; }}
QPushButton, QToolButton {{
    background-color: {colors['border']};
    color: {colors['text']};
    border: 1px solid {colors['hover']};
    border-radius: 5px;
    padding: 6px 12px;
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
QPushButton#sendButton, QToolButton#runButton {{
    background-color: {colors['accent']};
    color: {colors['button_text']};
    font-weight: 600;
}}
QPushButton#applyButton {{
    background-color: {colors['success']};
    color: {colors['button_text']};
    font-weight: 600;
}}
QPushButton#rejectButton {{
    background-color: {colors['error']};
    color: {colors['button_text']};
    font-weight: 600;
}}
QLineEdit, QTextEdit, QPlainTextEdit, QTextBrowser, QListWidget, QTreeView, QComboBox {{
    background-color: {colors['code_background']};
    color: {colors['text']};
    border: 1px solid {colors['border']};
    border-radius: 5px;
    selection-background-color: {colors['hover']};
    selection-color: {colors['text']};
}}
QLineEdit, QComboBox {{ background-color: {colors['panel']}; padding: 7px 9px; }}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QListWidget:focus, QTreeView:focus {{
    border-color: {colors['accent']};
}}
QTextEdit#logView {{ background-color: {colors['panel']}; }}
QTextEdit#codeView {{ background-color: {colors['code_background']}; }}
QListWidget::item {{ padding: 5px; border-radius: 3px; }}
QListWidget::item:selected {{ background-color: {colors['hover']}; }}
QTreeView {{ background-color: {colors['code_background']}; alternate-background-color: {colors['panel']}; }}
QTreeView::item {{ padding: 4px; border-radius: 3px; }}
QTreeView::item:selected {{ background-color: {colors['hover']}; color: {colors['text']}; }}
QProgressBar {{
    background-color: {colors['panel']};
    border: 1px solid {colors['border']};
    border-radius: 5px;
    min-height: 9px;
    max-height: 9px;
}}
QProgressBar::chunk {{ background-color: {colors['purple']}; border-radius: 4px; }}
QLabel#loadingLabel {{ color: {colors['purple']}; font-weight: 700; }}
QTabWidget::pane {{ border: 1px solid {colors['border']}; background: {colors['code_background']}; }}
QTabBar::tab {{
    background: {colors['panel']};
    color: {colors['muted']};
    border: 1px solid {colors['border']};
    padding: 6px 12px;
}}
QTabBar::tab:selected {{ color: {colors['text']}; background: {colors['hover']}; }}
QSplitter::handle {{ background-color: {colors['border']}; width: 4px; height: 4px; }}
QSplitter::handle:hover {{ background-color: {colors['accent']}; }}
QStatusBar {{ color: {colors['muted']}; border-top: 1px solid {colors['border']}; }}
QStatusBar::item {{ border: 0; }}
QScrollBar:vertical {{ background: {colors['panel']}; width: 12px; margin: 0; }}
QScrollBar::handle:vertical {{
    background: {colors['hover']};
    min-height: 24px;
    border-radius: 5px;
}}
QScrollBar::handle:vertical:hover {{ background: {colors['pressed']}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ height: 0; }}
QToolTip {{
    background: {colors['border']};
    color: {colors['text']};
    border: 1px solid {colors['hover']};
    padding: 4px;
}}
"""


DARK_THEME: str = _build_theme(DARK_COLORS)
LIGHT_THEME: str = _build_theme(LIGHT_COLORS)


def get_theme(theme_name: str) -> tuple[str, dict[str, str]]:
    """Return a stylesheet and semantic palette for a persisted theme name."""

    if theme_name == "light":
        return LIGHT_THEME, LIGHT_COLORS
    return DARK_THEME, DARK_COLORS
