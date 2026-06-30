"""
UI constants, colors, stylesheet, and helper functions.
"""

from PyQt5.QtWidgets import QLabel
from PyQt5.QtGui import QFont

ACCENT  = "#3B7DD8"
BG      = "#1E1E2E"
SURFACE = "#2A2A3E"
CARD    = "#313147"
TEXT    = "#E8E8F0"
MUTED   = "#9090A8"
GREEN   = "#4CAF82"
ORANGE  = "#E8994A"
RED_COL = "#E85A5A"


STYLE = f"""
QMainWindow, QWidget {{
    background-color: {BG};
    color: {TEXT};
    font-family: 'Segoe UI', 'SF Pro Display', Arial, sans-serif;
}}
QGroupBox {{
    border: 1px solid #3A3A52;
    border-radius: 8px;
    margin-top: 10px;
    padding-top: 8px;
    font-weight: bold;
    font-size: 12px;
    color: {MUTED};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
}}
QSlider::groove:horizontal {{
    height: 6px;
    background: #3A3A52;
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: {ACCENT};
    width: 18px;
    height: 18px;
    margin: -6px 0;
    border-radius: 9px;
}}
QSlider::sub-page:horizontal {{
    background: {ACCENT};
    border-radius: 3px;
}}
QPushButton {{
    background-color: {ACCENT};
    color: white;
    border: none;
    border-radius: 6px;
    padding: 8px 16px;
    font-size: 13px;
    font-weight: bold;
}}
QPushButton:hover {{
    background-color: #4A8FE8;
}}
QPushButton:pressed {{
    background-color: #2A6DC8;
}}
QPushButton#secondary {{
    background-color: {SURFACE};
    color: {TEXT};
    border: 1px solid #3A3A52;
}}
QPushButton#secondary:hover {{
    background-color: {CARD};
}}
QLabel {{
    color: {TEXT};
}}
QFrame#card {{
    background-color: {CARD};
    border-radius: 8px;
    border: 1px solid #3A3A52;
}}
QScrollArea {{
    border: none;
    background: transparent;
}}
QScrollBar:vertical {{
    background: {SURFACE};
    width: 6px;
    border-radius: 3px;
}}
QScrollBar::handle:vertical {{
    background: #4A4A62;
    border-radius: 3px;
    min-height: 20px;
}}
QDoubleSpinBox {{
    background-color: {CARD};
    color: {TEXT};
    border: 1px solid #3A3A52;
    border-radius: 4px;
    padding: 3px 6px;
    font-size: 12px;
    font-weight: bold;
}}
QDoubleSpinBox:disabled {{
    color: {MUTED};
    background-color: {SURFACE};
}}
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    width: 0px;
}}
QSplitter::handle {{
    background: #3A3A52;
    height: 3px;
}}
QTabWidget::pane {{
    border: 1px solid #3A3A52;
    border-radius: 6px;
    background: {BG};
}}
QTabBar::tab {{
    background: {SURFACE};
    color: {MUTED};
    padding: 6px 16px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    margin-right: 2px;
    font-size: 11px;
    font-weight: bold;
}}
QTabBar::tab:selected {{
    background: {BG};
    color: {TEXT};
    border: 1px solid #3A3A52;
    border-bottom: none;
}}
"""


def fmt_time(h, with_seconds=False):
    if with_seconds:
        total_s = round(h * 3600)
        hh = total_s // 3600
        rem = total_s % 3600
        mm = rem // 60
        ss = rem % 60
        return f"{hh}h {mm:02d}min {ss:02d}s"
    hh = int(h)
    mm = round((h - hh) * 60)
    if mm == 60:
        hh += 1; mm = 0
    return f"{hh}h {mm:02d}min"


def make_label(text, size=13, bold=False, color=None):
    lbl = QLabel(text)
    font = QFont()
    font.setPointSize(size)
    font.setBold(bold)
    lbl.setFont(font)
    if color:
        lbl.setStyleSheet(f"color: {color};")
    return lbl
