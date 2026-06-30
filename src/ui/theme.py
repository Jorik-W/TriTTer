"""TriTTer shared theme: one dark-flat palette + global stylesheet.

This is the single source of visual truth for the whole app. It promotes the
palette that originated in bike_estimator (plan/ui/constants.py) and extends the
stylesheet to cover every widget type the Analyze GUI uses (combo boxes, line
edits, tables, checkboxes, radio buttons, progress bars, the wizard step rail),
so both tabs look uniform.
"""

# ---- Palette ---------------------------------------------------------------
ACCENT  = "#3B7DD8"
BG      = "#1E1E2E"
SURFACE = "#2A2A3E"
CARD    = "#313147"
TEXT    = "#E8E8F0"
MUTED   = "#9090A8"
GREEN   = "#4CAF82"
ORANGE  = "#E8994A"
RED_COL = "#E85A5A"
BORDER  = "#3A3A52"


STYLE = f"""
QMainWindow, QWidget, QDialog {{
    background-color: {BG};
    color: {TEXT};
    font-family: 'Segoe UI', 'SF Pro Display', Arial, sans-serif;
    font-size: 12px;
}}
QGroupBox {{
    border: 1px solid {BORDER};
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
QLabel {{
    color: {TEXT};
    background: transparent;
}}
QSlider::groove:horizontal {{
    height: 6px;
    background: {BG};
    border: none;
    border-radius: 3px;
}}
QSlider::add-page:horizontal {{
    background: {BG};
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
QSlider:disabled::handle:horizontal {{
    background: {MUTED};
}}
QPushButton {{
    background-color: {ACCENT};
    color: white;
    border: none;
    border-radius: 6px;
    padding: 7px 14px;
    font-size: 12px;
    font-weight: bold;
}}
QPushButton:hover {{ background-color: #4A8FE8; }}
QPushButton:pressed {{ background-color: #2A6DC8; }}
QPushButton:disabled {{ background-color: {SURFACE}; color: {MUTED}; }}
QPushButton#secondary {{
    background-color: {SURFACE};
    color: {TEXT};
    border: 1px solid {BORDER};
}}
QPushButton#secondary:hover {{ background-color: {CARD}; }}
QFrame#card {{
    background-color: {CARD};
    border-radius: 8px;
    border: 1px solid {BORDER};
}}
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{
    background: {SURFACE};
    width: 8px;
    border-radius: 4px;
}}
QScrollBar::handle:vertical {{
    background: #4A4A62;
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{
    background: {SURFACE};
    height: 8px;
    border-radius: 4px;
}}
QScrollBar::handle:horizontal {{
    background: #4A4A62;
    border-radius: 4px;
    min-width: 24px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QDoubleSpinBox, QSpinBox, QLineEdit {{
    background-color: {CARD};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 3px 6px;
    font-size: 12px;
}}
QDoubleSpinBox:focus, QSpinBox:focus, QLineEdit:focus {{ border: 1px solid {ACCENT}; }}
QDoubleSpinBox:disabled, QSpinBox:disabled, QLineEdit:disabled {{
    color: {MUTED};
    background-color: {SURFACE};
}}
QComboBox {{
    background-color: {CARD};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 3px 8px;
    font-size: 12px;
}}
QComboBox:focus {{ border: 1px solid {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{
    background-color: {CARD};
    color: {TEXT};
    selection-background-color: {ACCENT};
    border: 1px solid {BORDER};
}}
QCheckBox, QRadioButton {{ color: {TEXT}; spacing: 6px; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px; }}
QCheckBox::indicator {{
    border: 1px solid {BORDER}; border-radius: 4px; background: {CARD};
}}
QCheckBox::indicator:checked {{ background: {ACCENT}; border: 1px solid {ACCENT}; }}
QRadioButton::indicator {{
    border: 1px solid {BORDER}; border-radius: 8px; background: {CARD};
}}
QRadioButton::indicator:checked {{ background: {ACCENT}; border: 4px solid {CARD}; }}
QTextEdit, QPlainTextEdit {{
    background-color: {SURFACE};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    font-family: 'Consolas', 'Cascadia Mono', monospace;
    font-size: 11px;
}}
QTableWidget, QTableView {{
    background-color: {SURFACE};
    alternate-background-color: {CARD};
    color: {TEXT};
    gridline-color: {BORDER};
    border: 1px solid {BORDER};
    border-radius: 6px;
}}
QHeaderView::section {{
    background-color: {CARD};
    color: {MUTED};
    padding: 4px 6px;
    border: none;
    border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
    font-weight: bold;
}}
QTableCornerButton::section {{ background-color: {CARD}; border: none; }}
QProgressBar {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    text-align: center;
    color: {TEXT};
    height: 16px;
}}
QProgressBar::chunk {{ background-color: {ACCENT}; border-radius: 5px; }}
QSplitter::handle {{ background: {BORDER}; }}
QTabWidget::pane {{
    border: 1px solid {BORDER};
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
    border: 1px solid {BORDER};
    border-bottom: none;
}}
/* Wizard step rail */
QListWidget#stepRail {{
    background: {SURFACE};
    border: none;
    border-right: 1px solid {BORDER};
    outline: 0;
    font-size: 12px;
}}
QListWidget#stepRail::item {{
    color: {MUTED};
    padding: 12px 14px;
    border-left: 3px solid transparent;
}}
QListWidget#stepRail::item:selected {{
    color: {TEXT};
    background: {CARD};
    border-left: 3px solid {ACCENT};
    font-weight: bold;
}}
QListWidget#stepRail::item:hover {{ color: {TEXT}; }}
QMenuBar {{ background: {SURFACE}; color: {TEXT}; }}
QMenuBar::item:selected {{ background: {CARD}; }}
QMenu {{ background: {CARD}; color: {TEXT}; border: 1px solid {BORDER}; }}
QMenu::item:selected {{ background: {ACCENT}; }}
QToolTip {{
    background-color: {CARD}; color: {TEXT};
    border: 1px solid {BORDER}; padding: 4px;
}}
"""


def apply_theme(app):
    """Apply the global dark-flat stylesheet to a QApplication."""
    # Fusion fully honours QSS sub-controls (slider groove/handle, etc.);
    # the native Windows style otherwise paints its own grey slider groove.
    try:
        app.setStyle("Fusion")
    except Exception:
        pass
    app.setStyleSheet(STYLE)
    try:
        _style_matplotlib()
    except Exception:
        pass


def _style_matplotlib():
    """Make matplotlib figures match the dark theme (Analyze plots)."""
    import matplotlib
    matplotlib.rcParams.update({
        "figure.facecolor": CARD,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": CARD,
        "text.color": TEXT,
        "axes.labelcolor": TEXT,
        "axes.edgecolor": BORDER,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "grid.color": BORDER,
        "axes.titlecolor": TEXT,
        "legend.facecolor": CARD,
        "legend.edgecolor": BORDER,
        "figure.autolayout": True,
    })
