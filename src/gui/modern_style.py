"""アプリケーション全体のモダンな外観（Fusion + ダークパレット + QSS）。"""

from __future__ import annotations

from PyQt6.QtGui import QColor, QFont, QPalette
from PyQt6.QtWidgets import QApplication

# アクセント: 青系（ホバー・選択・フォーカス）
_ACCENT = "#5b9cfa"
_ACCENT_MUTED = "#3d5a8a"
_BG = "#1a1d24"
_BG_ELEV = "#22262f"
_BG_INPUT = "#2a303c"
_BORDER = "#3d4555"
_TEXT = "#e6e9ef"
_TEXT_DIM = "#9aa3b6"


def apply_modern_theme(app: QApplication) -> None:
    """Fusion スタイルとダークテーマを適用する。"""

    app.setStyle("Fusion")

    font = app.font()
    if font.pointSizeF() <= 0:
        font.setPixelSize(13)
    else:
        font.setPointSizeF(max(font.pointSizeF(), 10.0))
    app.setFont(font)

    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(_BG))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(_TEXT))
    pal.setColor(QPalette.ColorRole.Base, QColor(_BG_INPUT))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(_BG_ELEV))
    pal.setColor(QPalette.ColorRole.Text, QColor(_TEXT))
    pal.setColor(QPalette.ColorRole.Button, QColor(_BG_ELEV))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(_TEXT))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(_ACCENT))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(_BG_ELEV))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(_TEXT))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(_TEXT_DIM))
    app.setPalette(pal)

    app.setStyleSheet(_QSS)


_QSS = f"""
    QWidget {{
        background-color: {_BG};
        color: {_TEXT};
    }}

    QMainWindow {{
        background-color: {_BG};
    }}

    QTabWidget::pane {{
        border: 1px solid {_BORDER};
        border-radius: 10px;
        background-color: {_BG_ELEV};
        top: -1px;
        padding: 8px;
    }}

    QTabBar::tab {{
        background-color: transparent;
        color: {_TEXT_DIM};
        border: none;
        border-bottom: 2px solid transparent;
        padding: 10px 18px;
        margin-right: 4px;
        font-weight: 500;
    }}

    QTabBar::tab:selected {{
        color: {_TEXT};
        border-bottom: 2px solid {_ACCENT};
    }}

    QTabBar::tab:hover:!selected {{
        color: {_TEXT};
        background-color: rgba(91, 156, 250, 0.08);
        border-radius: 6px 6px 0 0;
    }}

    QGroupBox {{
        font-weight: 600;
        font-size: 13px;
        border: 1px solid {_BORDER};
        border-radius: 10px;
        margin-top: 14px;
        padding: 18px 14px 14px 14px;
        background-color: {_BG};
    }}

    QGroupBox::title {{
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 14px;
        top: 2px;
        padding: 0 8px;
        color: {_ACCENT};
        background-color: {_BG};
    }}

    QPushButton {{
        background-color: {_BG_ELEV};
        color: {_TEXT};
        border: 1px solid {_BORDER};
        border-radius: 8px;
        padding: 8px 16px;
        min-height: 20px;
        font-weight: 500;
    }}

    QPushButton:hover {{
        background-color: rgba(91, 156, 250, 0.15);
        border-color: {_ACCENT_MUTED};
    }}

    QPushButton:pressed {{
        background-color: rgba(91, 156, 250, 0.28);
    }}

    QPushButton:disabled {{
        color: {_TEXT_DIM};
        border-color: {_BORDER};
        background-color: {_BG};
    }}

    QCheckBox {{
        spacing: 10px;
    }}

    QCheckBox::indicator {{
        width: 18px;
        height: 18px;
        border-radius: 4px;
        border: 1px solid {_BORDER};
        background-color: {_BG_INPUT};
    }}

    QCheckBox::indicator:checked {{
        background-color: {_ACCENT};
        border-color: {_ACCENT};
    }}

    QCheckBox::indicator:hover {{
        border-color: {_ACCENT};
    }}

    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
        background-color: {_BG_INPUT};
        color: {_TEXT};
        border: 1px solid {_BORDER};
        border-radius: 8px;
        padding: 6px 10px;
        min-height: 22px;
        selection-background-color: {_ACCENT};
    }}

    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
        border-color: {_ACCENT};
    }}

    QComboBox::drop-down {{
        border: none;
        width: 28px;
        border-top-right-radius: 8px;
        border-bottom-right-radius: 8px;
    }}

    QComboBox::down-arrow {{
        width: 0;
        height: 0;
        border-left: 5px solid transparent;
        border-right: 5px solid transparent;
        border-top: 6px solid {_TEXT_DIM};
        margin-right: 10px;
    }}

    QComboBox QAbstractItemView {{
        background-color: {_BG_ELEV};
        color: {_TEXT};
        border: 1px solid {_BORDER};
        border-radius: 8px;
        selection-background-color: {_ACCENT};
        padding: 4px;
        outline: none;
    }}

    QScrollArea {{
        border: none;
        background-color: transparent;
    }}

    QScrollBar:vertical {{
        background-color: {_BG};
        width: 12px;
        margin: 0;
        border-radius: 6px;
    }}

    QScrollBar::handle:vertical {{
        background-color: {_BORDER};
        min-height: 40px;
        border-radius: 6px;
        margin: 2px;
    }}

    QScrollBar::handle:vertical:hover {{
        background-color: {_ACCENT_MUTED};
    }}

    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}

    QScrollBar:horizontal {{
        background-color: {_BG};
        height: 12px;
        margin: 0;
        border-radius: 6px;
    }}

    QScrollBar::handle:horizontal {{
        background-color: {_BORDER};
        min-width: 40px;
        border-radius: 6px;
        margin: 2px;
    }}

    QTextBrowser, QTextEdit {{
        background-color: {_BG_INPUT};
        color: {_TEXT};
        border: 1px solid {_BORDER};
        border-radius: 10px;
        padding: 12px;
        selection-background-color: {_ACCENT};
    }}

    QLabel {{
        background-color: transparent;
        color: {_TEXT};
    }}

    QMenu {{
        background-color: {_BG_ELEV};
        color: {_TEXT};
        border: 1px solid {_BORDER};
        border-radius: 8px;
        padding: 6px;
    }}

    QMenu::item {{
        padding: 8px 28px;
        border-radius: 6px;
    }}

    QMenu::item:selected {{
        background-color: rgba(91, 156, 250, 0.25);
    }}

    QMenu::separator {{
        height: 1px;
        margin: 6px 8px;
        background-color: {_BORDER};
    }}

    QToolTip {{
        background-color: {_BG_ELEV};
        color: {_TEXT};
        border: 1px solid {_BORDER};
        border-radius: 6px;
        padding: 8px;
    }}
"""
