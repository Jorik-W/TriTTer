"""Graphical User Interface for CDA analyzer (PyQt5 Version)"""

# Standard library imports
import sys
import os
import json
import argparse
import logging
import threading
import faulthandler
import tempfile
from pathlib import Path
import traceback

_logger = logging.getLogger(__name__)

# Third-party imports
import pandas as pd
import numpy as np
import folium
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

# PyQt5 imports
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QLabel, QPushButton, QTextEdit, QLineEdit,
    QFileDialog, QMessageBox, QProgressBar, QScrollArea,
    QSplashScreen, QGridLayout, QFrame, QDialog, QSlider, QSpinBox, QCheckBox,
    QRadioButton, QButtonGroup, QGroupBox, QAbstractItemView, QHeaderView,
    QTableWidgetItem, QSizePolicy,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QUrl, QTimer, QRect, QByteArray
from PyQt5.QtGui import QFont, QIcon, QPixmap, QPainter, QBrush, QLinearGradient, QColor
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEngineSettings

# Local module imports
from icon import LOGO_BASE64
from fit_parser import FITParser
from analyzer import CDAAnalyzer
from weather import WeatherService
from elevation import ElevationService, OpenMeteoElevationService
from config import DEFAULT_PARAMETERS
from widgets import SliderRow, SectionHeader, MetricCard, CopyableTableWidget
from theme import MUTED, TEXT, ACCENT, GREEN, ORANGE, RED_COL, BORDER, SURFACE, CARD, apply_dwm_dark_titlebar

_CRASH_APP = None
_CRASH_LOG_PATH = Path.cwd() / "cda_analyzer_crash.log"
_STAGE_LOG_PATH = Path.cwd() / "cda_analyzer_stage.log"
_FILE_LOG_ENABLED = False


def _append_crash_log(message):
    """Best-effort append to crash log file."""
    if not _FILE_LOG_ENABLED:
        return
    try:
        with _CRASH_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(message + "\n")
    except Exception:
        pass


def _mark_stage(stage):
    """Persist last known execution stage for native crash diagnostics."""
    if not _FILE_LOG_ENABLED:
        return
    try:
        with _STAGE_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(stage + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    except Exception:
        pass


def _show_fatal_dialog(title, message):
    """Show a fatal error dialog if QApplication is available."""
    try:
        if _CRASH_APP is not None:
            QMessageBox.critical(None, title, message)
    except Exception:
        pass


def _python_excepthook(exc_type, exc_value, exc_tb):
    """Handle uncaught exceptions from Python main thread."""
    tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    text = f"[UNCAUGHT PYTHON EXCEPTION]\n{tb}"
    _append_crash_log(text)
    _logger.critical(text)
    extra = f"\n\nSee log: {_CRASH_LOG_PATH}" if _FILE_LOG_ENABLED else ""
    _show_fatal_dialog("Unhandled Error", f"An unexpected error occurred.\n\n{exc_value}{extra}")


def _threading_excepthook(args):
    """Handle uncaught exceptions from Python threads."""
    tb = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
    text = f"[UNCAUGHT THREAD EXCEPTION] thread={args.thread.name}\n{tb}"
    _append_crash_log(text)
    _logger.critical(text)
    extra = f"\n\nSee log: {_CRASH_LOG_PATH}" if _FILE_LOG_ENABLED else ""
    _show_fatal_dialog("Background Thread Error", f"A background thread crashed.\n\n{args.exc_value}{extra}")


def _qt_message_handler(mode, context, message):
    """Capture Qt warnings/errors that may not raise Python exceptions."""
    # Never let exceptions escape a Qt message handler.
    # Escaping here can terminate the process without a Python traceback.
    try:
        try:
            mode_name = {
                0: "QtDebugMsg",
                1: "QtWarningMsg",
                2: "QtCriticalMsg",
                3: "QtFatalMsg",
                4: "QtInfoMsg",
            }.get(int(mode), f"QtMsg({int(mode)})")
        except Exception:
            mode_name = "QtMsg"

        text = f"[{mode_name}] {message}"
        _append_crash_log(text)
    except Exception:
        pass


def _install_global_error_reporting(app, enable_file_log=False, crash_log_path=None):
    """Install global hooks so crashes always leave a report."""
    global _CRASH_APP, _FILE_LOG_ENABLED, _CRASH_LOG_PATH, _STAGE_LOG_PATH
    _CRASH_APP = app
    _FILE_LOG_ENABLED = bool(enable_file_log)

    if crash_log_path:
        _CRASH_LOG_PATH = Path(crash_log_path)
        _STAGE_LOG_PATH = _CRASH_LOG_PATH.with_name(_CRASH_LOG_PATH.stem + "_stage.log")

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    if _FILE_LOG_ENABLED:
        # Ensure logging has a persistent file sink.
        has_file_handler = any(
            isinstance(h, logging.FileHandler) and getattr(h, "baseFilename", "") == str(_CRASH_LOG_PATH)
            for h in root_logger.handlers
        )
        if not has_file_handler:
            file_handler = logging.FileHandler(_CRASH_LOG_PATH, encoding="utf-8")
            file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
            root_logger.addHandler(file_handler)

    # Dump Python fault traces (segfaults, aborts) where possible.
    try:
        if _FILE_LOG_ENABLED:
            crash_file = _CRASH_LOG_PATH.open("a", encoding="utf-8")
            faulthandler.enable(crash_file, all_threads=True)
        else:
            faulthandler.enable(all_threads=True)
    except Exception as e:
        _append_crash_log(f"[WARN] Could not enable faulthandler: {e}")

    # Python-level uncaught exceptions.
    sys.excepthook = _python_excepthook
    try:
        threading.excepthook = _threading_excepthook
    except Exception:
        pass

    # Qt-level warnings/errors are redirected only when file logging is enabled.
    if _FILE_LOG_ENABLED:
        try:
            from PyQt5.QtCore import qInstallMessageHandler
            qInstallMessageHandler(_qt_message_handler)
        except Exception as e:
            _append_crash_log(f"[WARN] Could not install Qt message handler: {e}")

class WorkerThread(QThread):
    """Background thread for analysis"""
    finished = pyqtSignal(object, str, object)  # results, error, preprocessed_segments
    status = pyqtSignal(str)

    def __init__(self, analyzer, ride_data, weather_service):
        super().__init__()
        self.analyzer = analyzer
        self.ride_data = ride_data
        self.weather_service = weather_service

    def _emit_status(self, message):
        self.status.emit(message)
        _logger.info(message)

    def _prepare_elevation_for_analysis(self):
        elevation_source = self.analyzer.parameters.get('elevation_source', 'fit_only')
        
        has_fit_altitude = (
            'altitude_fit' in self.ride_data.columns and
            self.ride_data['altitude_fit'].notna().any()
        )

        has_open_elevation = (
            'altitude_open_elevation' in self.ride_data.columns and
            self.ride_data['altitude_open_elevation'].notna().any()
        )
        has_open_meteo = (
            'altitude_open_meteo' in self.ride_data.columns and
            self.ride_data['altitude_open_meteo'].notna().any()
        )

        if elevation_source in ('open_elevation', 'open_meteo'):
            if elevation_source == 'open_elevation' and has_open_elevation:
                self.ride_data['altitude'] = self.ride_data['altitude_open_elevation']
                self.ride_data['altitude_api'] = self.ride_data['altitude_open_elevation']
                if 'slope_degrees_open_elevation' in self.ride_data.columns:
                    self.ride_data['slope_degrees'] = self.ride_data['slope_degrees_open_elevation']
                source_name = 'Open-Elevation API'
                self.analyzer.elevation_source = f'{source_name} (fetched at file load)'
                self._emit_status(f"Elevation source: {source_name} (fetched at file load)")
            elif elevation_source == 'open_meteo' and has_open_meteo:
                self.ride_data['altitude'] = self.ride_data['altitude_open_meteo']
                self.ride_data['altitude_api'] = self.ride_data['altitude_open_meteo']
                if 'slope_degrees_open_meteo' in self.ride_data.columns:
                    self.ride_data['slope_degrees'] = self.ride_data['slope_degrees_open_meteo']
                source_name = 'Open-Meteo Elevation API'
                self.analyzer.elevation_source = f'{source_name} (fetched at file load)'
                self._emit_status(f"Elevation source: {source_name} (fetched at file load)")
            elif has_fit_altitude:
                self.ride_data['altitude'] = self.ride_data['altitude_fit']
                if 'slope_degrees_fit' in self.ride_data.columns:
                    self.ride_data['slope_degrees'] = self.ride_data['slope_degrees_fit']
                self.analyzer.elevation_source = 'FIT file (API failed)'
                self._emit_status(f"Elevation API selected, but no altitude data available: using FIT altitude")
            else:
                self.analyzer.elevation_source = 'Unknown (no altitude data)'
                self._emit_status("No elevation data available")
        else:  # 'fit_only'
            if has_fit_altitude:
                self.ride_data['altitude'] = self.ride_data['altitude_fit']
                if 'slope_degrees_fit' in self.ride_data.columns:
                    self.ride_data['slope_degrees'] = self.ride_data['slope_degrees_fit']
            self.analyzer.elevation_source = 'FIT file'
            self._emit_status("Elevation source: FIT file")

    def run(self):
        try:
            _mark_stage("worker:run:start")
            self._prepare_elevation_for_analysis()
            _mark_stage("worker:run:after_elevation")
            # Preprocess the ride data first
            preprocessed_segments = self.analyzer.preprocess_ride_data(self.ride_data, self.weather_service)
            _mark_stage("worker:run:after_preprocess")
            # Then analyze with the preprocessed segments
            results = self.analyzer.analyze_ride(self.ride_data, self.weather_service, preprocessed_segments)
            _mark_stage("worker:run:after_analyze")
            self.finished.emit(results, None, preprocessed_segments)
        except Exception as e:
            _mark_stage("worker:run:exception")
            tb = traceback.format_exc()
            _append_crash_log(f"[WORKER EXCEPTION]\n{tb}")
            _logger.exception("Worker thread failed")
            self.finished.emit(None, f"{e}\n\n{tb}", None)

class CustomProgress(QProgressBar):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pos = 0
        self._animate = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)

    def setRange(self, minimum, maximum):
        super().setRange(minimum, maximum)
        if minimum == 0 and maximum == 0:
            self._startIndeterminate()
        else:
            self._stopIndeterminate()

    def _startIndeterminate(self):
        if not self._animate:
            self._animate = True
            self._pos = 0
            self._timer.start(30)
            self.update()

    def _stopIndeterminate(self):
        if self._animate:
            self._animate = False
            self._timer.stop()
            self.update()

    def _advance(self):
        self._pos += 5
        if self._pos > self.width():
            self._pos = -self.width() // 10
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        rect = self.rect()

        # Background
        painter.setBrush(QColor("#5a5a72"))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(rect, 5, 5)

        if self._animate:
            # 10% wide moving chunk
            chunk_width = rect.width() // 10
            chunk_rect = QRect(self._pos, rect.y(), chunk_width, rect.height())

            gradient = QLinearGradient(chunk_rect.topLeft(), chunk_rect.topRight())
            gradient.setColorAt(0, QColor("#2196F3"))
            gradient.setColorAt(1, QColor("#21CBF3"))

            painter.setBrush(QBrush(gradient))
            painter.drawRoundedRect(chunk_rect, 5, 5)

            # Optional: show text as "Loading..."
            painter.setPen(Qt.black)
            painter.drawText(rect, Qt.AlignCenter, "Loading...")

        else:
            # Normal percentage mode
            if self.maximum() > 0:
                fill_width = int(rect.width() * self.value() / self.maximum())
                if fill_width > 0:
                    chunk_rect = QRect(rect.x(), rect.y(), fill_width, rect.height())

                    gradient = QLinearGradient(chunk_rect.topLeft(), chunk_rect.topRight())
                    gradient.setColorAt(0, QColor("#2196F3"))
                    gradient.setColorAt(1, QColor("#21CBF3"))

                    painter.setBrush(QBrush(gradient))
                    painter.drawRoundedRect(chunk_rect, 5, 5)

            # Draw progress text
            painter.setPen(Qt.black)
            painter.drawText(rect, Qt.AlignCenter, f"{self.value()}%")

def resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller."""
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.abspath(".")
    path = os.path.join(base_path, relative_path)
    if not os.path.exists(path):
        alt_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)
        if os.path.exists(alt_path):
            path = alt_path
        else:
            _logger.warning("Resource not found: %s", path)
    return path

class GUIInterface(QMainWindow):
    windEffectChanged = pyqtSignal(float)   # emitted when user changes WEF slider

    def __init__(self, app):
        super().__init__()
        self.app = app  # Store reference
        self.setWindowTitle("TriTTer")
        self.resize(1200, 1000)
        self._set_window_icon()

        # Initialize data
        self.fit_file_path = None
        self.parameters = DEFAULT_PARAMETERS.copy()
        self.ride_data = None
        self.analysis_results = None
        self.preprocessed_segments = None
        self.segment_data_map = {}
        self.current_figure = None
        self.current_canvas = None
        self.worker = None
        self._map_html_path = None
        self.load_weather_api_on_file_load = True
        self.load_open_elevation_on_file_load = True
        self.load_open_meteo_on_file_load = False
        self.weather_api_loaded = False
        self.elevation_api_loaded = False
        self.preloaded_weather_samples = []

        self.home_directory = os.path.expanduser('~')
        self.downloads_path = os.path.join(self.home_directory, 'Downloads')
        self.last_browse_path = os.path.abspath('~')

        self.analyzer = CDAAnalyzer(self.parameters)
        self.weather_service = WeatherService()

        # Setup UI immediately — window is already about to be shown
        self._setup_ui()

        # Bring to front
        self.raise_()
        self.activateWindow()

    def _setup_ui(self):
        """Setup the user interface — single-page layout (no wizard)."""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)

        # ── Hidden compatibility refs (existing code writes to these) ────
        self.file_status = QTextEdit()       # invisible sink – _load_fit_file logs here
        self.file_frame  = QWidget()
        self.file_label  = QLabel()
        self.parameters_frame = QWidget()
        self.results_frame    = QWidget()

        # File-load API checkboxes (not shown; defaults read in _load_fit_file)
        self.load_weather_api_checkbox = QCheckBox()
        self.load_weather_api_checkbox.setChecked(True)
        self.load_open_elevation_checkbox = QCheckBox()
        self.load_open_elevation_checkbox.setChecked(True)
        self.load_open_meteo_checkbox = QCheckBox()
        self.load_open_meteo_checkbox.setChecked(False)

        # Wind-effect SliderRow (not shown; _save_parameters reads/writes it)
        self.wind_effect_slider = SliderRow(
            "Wind effect factor", 0.00, 1.50,
            self.analyzer.parameters.get('wind_effect_factor', 0.40),
            0.01, 2, "")
        self.wind_effect_slider.valueChanged.connect(self._on_wind_effect_slider_moved)
        self.wind_effect_slider.interactionFinished.connect(self._on_wind_effect_changed)
        self.wind_effect_value_label = QLabel()

        # ── Top action bar ────────────────────────────────────────────────
        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)

        run_btn = QPushButton("▶  Run Analysis")
        run_btn.setFixedWidth(130)
        run_btn.clicked.connect(self._run_analysis)
        top_bar.addWidget(run_btn)

        self.progress = CustomProgress()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setAlignment(Qt.AlignCenter)
        top_bar.addWidget(self.progress, 1)

        self.analysis_status = QLabel("Ready to analyse")
        self.analysis_status.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        top_bar.addWidget(self.analysis_status)

        about_btn = QPushButton("?")
        about_btn.setFixedSize(25, 25)
        about_btn.setToolTip("About this program")
        about_btn.clicked.connect(self._show_about_dialog)
        top_bar.addWidget(about_btn)
        layout.addLayout(top_bar)

        # ── Collapsible Advanced settings strip ───────────────────────────
        self._adv_toggle = QPushButton("▶  Advanced settings")
        self._adv_toggle.setObjectName("secondary")
        self._adv_toggle.setCheckable(True)
        self._adv_toggle.setChecked(False)
        self._adv_toggle.toggled.connect(self._toggle_advanced)
        layout.addWidget(self._adv_toggle)

        self._adv_widget = QWidget()
        self._adv_widget.setVisible(False)
        adv_layout = QVBoxLayout(self._adv_widget)
        adv_layout.setContentsMargins(4, 4, 4, 4)
        adv_layout.setSpacing(4)
        self._build_advanced_params(adv_layout)
        layout.addWidget(self._adv_widget)

        # ── Results notebook ──────────────────────────────────────────────
        self.results_notebook = QTabWidget()
        layout.addWidget(self.results_notebook, 1)

        self._build_summary_tab()
        self._build_map_tab()
        self._build_plots_tab()

    # ---- Advanced params ------------------------------------------------
    def _build_advanced_params(self, adv_layout):
        """Build the collapsible Advanced settings strip."""
        # (min, max, step, decimals, suffix) per numeric parameter.
        param_meta = {
            'rider_mass':              (40.0, 120.0, 0.5, 1, " kg"),
            'bike_mass':               (4.0, 20.0, 0.1, 1, " kg"),
            'rolling_resistance':      (0.001, 0.020, 0.0005, 4, ""),
            'drivetrain_loss':         (0.0, 10.0, 0.1, 1, " %"),
            'min_segment_length':      (10, 1000, 10, 0, " m"),
            'min_duration':            (5, 120, 5, 0, " s"),
            'min_speed':               (0.0, 20.0, 0.1, 1, " m/s"),
            'max_speed':               (5.0, 30.0, 0.1, 1, " m/s"),
            'max_slope_variation':     (0.0, 30.0, 0.5, 1, " \u00b0"),
            'speed_steady_threshold':  (0.0, 2.0, 0.05, 2, " m/s"),
            'power_steady_threshold':  (0.0, 500.0, 10.0, 0, " W"),
            'slope_steady_threshold':  (0.0, 20.0, 0.5, 1, " \u00b0"),
            'cda_keep_percent':        (10.0, 100.0, 5.0, 0, " %"),
            'subsegment_min_duration_s': (1.0, 30.0, 1.0, 0, " s"),
            'subsegment_min_points':   (3, 50, 1, 0, ""),
            'wind_effect_factor':      (0.0, 1.50, 0.01, 2, ""),
        }

        # Rider/bike sliders exist in param_entries for apply_rider but are not
        # added to any visible layout.
        rider_keys  = {'rider_mass', 'bike_mass', 'rolling_resistance', 'drivetrain_loss'}
        # Advanced params shown in the collapsible strip.
        adv_groups = [
            ("Segment detection", ['min_segment_length', 'min_duration',
                                    'min_speed', 'max_speed']),
            ("Steady-state thresholds", ['speed_steady_threshold',
                                          'power_steady_threshold']),
            ("CdA filtering", ['cda_keep_percent']),
        ]
        hidden_always = {
            'weather_sample_distance_m', 'elevation_source', 'wind_effect_factor',
            'subsegment_min_duration_s', 'subsegment_min_points',
            'slope_steady_threshold', 'max_slope_variation',
        }

        self.param_entries   = {}
        self.param_checkboxes = {}
        self._param_percent_keys = {'drivetrain_loss'}

        # Create rider/bike SliderRows (hidden – for apply_rider / _save_parameters)
        for key in rider_keys:
            if key not in self.parameters or isinstance(self.parameters[key], bool):
                continue
            lo, hi, step, dec, suffix = param_meta[key]
            disp = float(self.parameters[key]) * 100.0 if key in self._param_percent_keys else float(self.parameters[key])
            self.param_entries[key] = SliderRow(
                key.replace('_', ' ').title(), lo, hi, disp, step,
                decimals=dec, suffix=suffix, label_width=200)

        # Create and add visible advanced SliderRows
        for group_title, keys in adv_groups:
            rows = []
            for key in keys:
                if key not in self.parameters or key in hidden_always or isinstance(self.parameters[key], bool):
                    continue
                lo, hi, step, dec, suffix = param_meta[key]
                disp = float(self.parameters[key]) * 100.0 if key in self._param_percent_keys else float(self.parameters[key])
                row = SliderRow(key.replace('_', ' ').title(), lo, hi,
                                disp, step, decimals=dec, suffix=suffix,
                                label_width=200)
                self.param_entries[key] = row
                rows.append(row)
            if rows:
                adv_layout.addWidget(SectionHeader(group_title))
                for row in rows:
                    adv_layout.addWidget(row)

        # Boolean parameters as checkboxes
        bool_keys = [k for k, v in self.parameters.items()
                     if isinstance(v, bool) and k not in hidden_always]
        if bool_keys:
            adv_layout.addWidget(SectionHeader("Weather settings"))
            for key in bool_keys:
                row = QHBoxLayout()
                row.setContentsMargins(0, 2, 0, 2)
                lbl = QLabel(key.replace('_', ' ').title())
                lbl.setFixedWidth(200)
                cb = QCheckBox()
                cb.setChecked(bool(self.parameters[key]))
                self.param_checkboxes[key] = cb
                row.addWidget(lbl)
                row.addWidget(cb)
                row.addStretch()
                adv_layout.addLayout(row)

        adv_layout.addWidget(self.wind_effect_slider)

        # Elevation source radio buttons
        adv_layout.addWidget(SectionHeader("Elevation source (analysis)"))
        elev_row = QHBoxLayout()
        elev_row.setSpacing(10)
        elev_lbl = QLabel("Elevation Source:")
        elev_lbl.setFixedWidth(200)
        elev_row.addWidget(elev_lbl)

        self.analysis_elevation_source_group = QButtonGroup(self)
        self.analysis_open_elevation_radio = QRadioButton("Open-Elevation")
        self.analysis_open_meteo_radio      = QRadioButton("Open-Meteo")
        self.analysis_fit_radio             = QRadioButton("FIT elevation")

        self.analysis_elevation_source_group.addButton(self.analysis_open_elevation_radio, 0)
        self.analysis_elevation_source_group.addButton(self.analysis_open_meteo_radio,      1)
        self.analysis_elevation_source_group.addButton(self.analysis_fit_radio,             2)

        src = str(self.parameters.get('elevation_source', 'fit_only'))
        if src == 'open_meteo':
            self.analysis_open_meteo_radio.setChecked(True)
        elif src == 'open_elevation':
            self.analysis_open_elevation_radio.setChecked(True)
        else:  # 'fit_only' (default)
            self.analysis_fit_radio.setChecked(True)

        elev_row.addWidget(self.analysis_open_elevation_radio)
        elev_row.addWidget(self.analysis_open_meteo_radio)
        elev_row.addWidget(self.analysis_fit_radio)
        elev_row.addStretch()
        adv_layout.addLayout(elev_row)

    def _toggle_advanced(self, checked: bool):
        self._adv_widget.setVisible(checked)
        self._adv_toggle.setText(
            "▼  Advanced settings" if checked else "▶  Advanced settings")

    # ---- Result sub-tabs ------------------------------------------------
    def _build_summary_tab(self):
        self.summary_frame = QWidget()
        outer = QVBoxLayout(self.summary_frame)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        # Status label shown while no results are available
        self.summary_status_label = QLabel("Run analysis to see results here")
        self.summary_status_label.setStyleSheet(
            f"color: {MUTED}; font-size: 13px; font-style: italic; padding: 20px;"
        )
        self.summary_status_label.setAlignment(Qt.AlignCenter)
        outer.addWidget(self.summary_status_label)

        # ── Results panel (hidden until analysis completes) ───────────────
        self._summary_results_widget = QWidget()
        self._summary_results_widget.setVisible(False)
        results_layout = QVBoxLayout(self._summary_results_widget)
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_layout.setSpacing(8)

        # Metric cards row
        cards_row = QHBoxLayout()
        cards_row.setSpacing(8)
        self.card_cda           = MetricCard("Weighted CdA",      "\u2014", accent=True)
        self.card_cda_std       = MetricCard("CdA std dev",       "\u2014")
        self.card_dist_ridden   = MetricCard("Distance ridden",   "\u2014")
        self.card_dist_analysed = MetricCard("Distance analysed", "\u2014")
        self.card_vw            = MetricCard("Avg wind v\u1d64",  "\u2014")
        self.card_wind_dir      = MetricCard("Wind dir",          "\u2014")
        self.card_np            = MetricCard("NP",                "\u2014")
        self.card_avg_pwr       = MetricCard("Avg power",         "\u2014")
        for c in [
            self.card_cda, self.card_cda_std,
            self.card_dist_ridden, self.card_dist_analysed,
            self.card_vw, self.card_wind_dir,
            self.card_np, self.card_avg_pwr,
        ]:
            cards_row.addWidget(c)
        results_layout.addLayout(cards_row)

        # ── Collapsible details group box ─────────────────────────────────
        self.details_box = QGroupBox("Details")
        self.details_box.setCheckable(True)
        self.details_box.setChecked(False)
        self.details_box.setStyleSheet(
            f"QGroupBox {{ color: {MUTED}; font-size: 11px; border: 1px solid {BORDER}; "
            f"border-radius: 4px; margin-top: 6px; padding-top: 4px; }}"
            f"QGroupBox::title {{ subcontrol-origin: margin; left: 8px; }}"
        )
        details_outer_layout = QVBoxLayout(self.details_box)
        details_outer_layout.setContentsMargins(6, 4, 6, 4)
        self.details_content = QWidget()
        details_inner = QVBoxLayout(self.details_content)
        details_inner.setContentsMargins(0, 0, 0, 0)
        self.details_text = QTextEdit()
        self.details_text.setReadOnly(True)
        self.details_text.setMaximumHeight(220)
        self.details_text.setStyleSheet(
            f"QTextEdit {{ background: transparent; color: {MUTED}; "
            f"font-size: 11px; border: none; font-family: monospace; }}"
        )
        details_inner.addWidget(self.details_text)
        details_outer_layout.addWidget(self.details_content)
        self.details_box.toggled.connect(self.details_content.setVisible)
        self.details_content.setVisible(False)
        results_layout.addWidget(self.details_box)

        # ── Segment table ─────────────────────────────────────────────────
        self.segment_box = QGroupBox("Segments")
        self.segment_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        seg_layout = QVBoxLayout(self.segment_box)
        seg_layout.setContentsMargins(8, 8, 8, 8)
        seg_layout.setSpacing(2)
        self.segment_table = CopyableTableWidget(0, 12)
        self.segment_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.segment_table.setMinimumHeight(200)
        self.segment_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.segment_table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.segment_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.segment_table.setAlternatingRowColors(True)
        self.segment_table.setShowGrid(False)
        self.segment_table.verticalHeader().setVisible(False)
        self.segment_table.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.segment_table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft)
        self.segment_table.horizontalHeader().setHighlightSections(False)
        self.segment_table.horizontalHeader().setMinimumHeight(22)
        self.segment_table.horizontalHeader().setStyleSheet(
            f"QHeaderView::section {{ background: transparent; color: {MUTED}; "
            f"border: 0; padding: 2px 4px; font-size: 10px; }}"
        )
        self.segment_table.setStyleSheet(
            f"""
            QTableWidget {{
                background: transparent;
                color: {TEXT};
                border: 0;
                alternate-background-color: #20203a;
                selection-background-color: #3b3b58;
                selection-color: #f3f4ff;
                gridline-color: transparent;
                font-size: 11px;
                font-family: monospace;
            }}
            QTableWidget::item {{
                padding: 3px 6px;
            }}
            """
        )
        seg_layout.addWidget(self.segment_table)
        results_layout.addWidget(self.segment_box, 1)

        outer.addWidget(self._summary_results_widget, 1)
        self.results_notebook.addTab(self.summary_frame, "Summary")

    def _build_map_tab(self):
        self.map_frame  = QWidget()
        map_layout = QVBoxLayout(self.map_frame)
        self.map_webview = QWebEngineView()
        self.map_webview.setMinimumHeight(400)
        self.map_webview.settings().setAttribute(
            QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
        map_layout.addWidget(self.map_webview)
        self.map_refresh_btn = QPushButton("Generate / Refresh Map")
        self.map_refresh_btn.clicked.connect(self._generate_map)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(self.map_refresh_btn)
        btn_row.addStretch()
        map_layout.addLayout(btn_row)
        self.results_notebook.addTab(self.map_frame, "Map")

    def _build_plots_tab(self):
        self.plot_frame  = QWidget()
        plot_layout = QVBoxLayout(self.plot_frame)
        self.plot_label = QLabel("Plots will be displayed here after analysis")
        self.plot_label.setAlignment(Qt.AlignCenter)
        plot_layout.addWidget(self.plot_label)
        self.plot_button = QPushButton("Generate Plots")
        self.plot_button.clicked.connect(self._generate_plots)
        plot_layout.addWidget(self.plot_button)
        self.results_notebook.addTab(self.plot_frame, "Plots")

    def load_file(self, path: str):
        """Load a file into the Analyse pipeline (called from the Open File tab).

        Sets ``self.fit_file_path`` and triggers the internal FIT load sequence,
        exactly as if the user had browsed to the file inside the File tab.
        """
        if not path or not os.path.isfile(path):
            return
        self.fit_file_path = str(path)
        from pathlib import Path as _Path
        if hasattr(self, "file_label"):
            self.file_label.setText(_Path(path).name)
        self._load_fit_file()

    def apply_rider(self, rider):
        """Apply a selected rider profile's parameters to the Analyze form.

        The Profile tab is the single source of truth for rider parameters;
        selecting a rider here pushes mass/Crr/drivetrain/wind values into the
        parameter widgets and rebuilds the analyzer.
        """
        if rider is None:
            return
        try:
            overrides = rider.to_analyze_overrides()
        except AttributeError:
            return
        self.selected_rider_name = getattr(rider, "name", None)
        for key, value in overrides.items():
            self.parameters[key] = value
            entry = getattr(self, "param_entries", {}).get(key)
            if entry is not None:
                try:
                    disp = (float(value) * 100.0
                            if key in getattr(self, '_param_percent_keys', set())
                            else float(value))
                    entry.set_value(disp, silent=True)
                except (TypeError, ValueError):
                    pass
        # Rebuild analyzer with the new rider parameters.
        try:
            self.analyzer = CDAAnalyzer(self.parameters)
        except Exception:
            _logger.exception("Failed to rebuild analyzer after rider change")

    def update_parameters(self, params: dict):
        """Update analyzer parameters and sync UI widgets.

        Called by app_shell to push WEF (and any other shared params) into the
        Analyze tab without rebuilding the full analyzer object.
        """
        self.parameters.update(params)
        try:
            self.analyzer.update_parameters(params)
        except Exception:
            pass
        if 'wind_effect_factor' in params:
            try:
                self.wind_effect_slider.set_value(
                    float(params['wind_effect_factor']), silent=True)
            except Exception:
                pass

    def apply_weather_from_tab(self, cfg: dict):
        """Apply weather settings from the shared WeatherTab to Analyze.

        Updates wind_effect_factor and — for manual source — overrides the
        preloaded weather samples so the next analysis uses manual conditions.
        """
        wef = float(cfg.get('wind_effect_factor', 0.40))
        self.update_parameters({'wind_effect_factor': wef})

        source = cfg.get('source', 'manual')
        if source == 'manual':
            t  = float(cfg.get('temperature_c',      15.0))
            p  = float(cfg.get('pressure_hpa',       1013.25))
            ws = float(cfg.get('wind_speed_ms',      0.0))
            wd = float(cfg.get('wind_direction_deg', 0.0))
            manual_sample = {
                'distance': 0.0,
                'weather_data': {
                    'temperature':    t,
                    'pressure':       p,
                    'wind_speed':     ws,
                    'wind_direction': wd,
                },
            }
            self.preloaded_weather_samples = [manual_sample]
            try:
                self.analyzer.preloaded_weather_samples = [manual_sample]
            except Exception:
                pass
        elif source == 'api':
            api_result = cfg.get('api_result')
            if api_result and api_result.get('weather_samples'):
                # Convert fetch_weather_samples format → analyzer preloaded format.
                # fetch: {distance, latitude, longitude, when, weather:{...}}
                # analyzer: {distance, timestamp, weather_data:{...}}
                analyze_samples = []
                for s in api_result['weather_samples']:
                    w = s.get('weather') or {}
                    analyze_samples.append({
                        'distance':    s.get('distance', 0.0),
                        'timestamp':   s.get('when'),
                        'weather_data': {
                            'temperature':    w.get('temperature', 20.0),
                            'pressure':       w.get('pressure', 1013.25),
                            'wind_speed':     w.get('wind_speed', 0.0),
                            'wind_direction': w.get('wind_direction', 0.0),
                        },
                    })
                self.preloaded_weather_samples = analyze_samples
                try:
                    self.analyzer.preloaded_weather_samples = analyze_samples
                except Exception:
                    pass
            # else: leave preloaded_weather_samples as-is (from file load).

    def _show_about_dialog(self):
        dialog = QDialog(self, flags=Qt.Dialog | Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        dialog.setWindowTitle("About CdA Analyzer")
        dialog.setFixedWidth(400)  # Optional: fixed width

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # Logo
        logo_data = QByteArray.fromBase64(LOGO_BASE64.encode('utf-8'))
        pixmap = QPixmap()
        pixmap.loadFromData(logo_data)
        pixmap = pixmap.scaledToWidth(80, Qt.SmoothTransformation)  # Smaller logo
        logo_label = QLabel()
        logo_label.setPixmap(pixmap)
        logo_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(logo_label)

        # Text
        about_text = """
        <b>CdA Analyzer</b><br>
        Version 1.0<br><br>
        <b>Author:</b> Jorik Wittevrongel<br>
        <b>GitHub:</b> <a href='https://github.com/Jorik-W/TriTTer'>https://github.com/JorikWit/CdA-Analyser</a><br><br>

        This program is licensed under the
        MIT License.<br>
        See the LICENSE file for details.<br><br>

        <b>Icon credit:</b><br>
        Time Trial Bike icon by
        <a href='https://www.flaticon.com/authors/izwar-muis'>Izwar Muis</a>
        from
        <a href='https://www.flaticon.com/free-icon/time-trial-bike_17736701'>Flaticon</a>
        (used with attribution).<br><br>

        <b>Third-party libraries:</b><br>
        - fitparse (BSD License)<br>
        - folium (MIT License)<br>
        - geopy (MIT License)<br>
        - matplotlib (Matplotlib License, BSD-compatible)<br>
        - numpy (BSD-3-Clause)<br>
        - pandas (BSD-3-Clause)<br>
        - Pillow (PIL Software License, similar to MIT)<br>
        - PyQt5 (GPL v3)<br>
        - PyQt5_sip (GPL v3)<br>
        - requests (Apache-2.0)<br>
        - scipy (BSD License)<br>
        """
        text_label = QLabel(about_text)
        text_label.setTextFormat(Qt.RichText)
        text_label.setOpenExternalLinks(True)  # Make GitHub link clickable
        text_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        layout.addWidget(text_label)

        # OK button
        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(dialog.accept)
        ok_btn.setFixedWidth(80)
        ok_btn.setDefault(True)
        ok_btn.setAutoDefault(True)
        ok_btn.setCursor(Qt.PointingHandCursor)
        layout.addWidget(ok_btn, alignment=Qt.AlignCenter)

        dialog.exec_()

    def _browse_fit_file(self):
        # Adjust to proper default path
        if not os.path.exists(self.last_browse_path) or self.last_browse_path == '~':
            self.last_browse_path = os.path.join(self.home_directory, 'Downloads')
        
        path, _ = QFileDialog.getOpenFileName(
            self, "Select FIT File", self.last_browse_path, "FIT files (*.fit);;All files (*)"
        )
        
        if path:
            self.fit_file_path = path
            self.last_browse_path = os.path.dirname(path)
            self.file_label.setText(Path(path).name)
            self._load_fit_file()

    def _load_fit_file(self):
        if not self.fit_file_path:
            QMessageBox.critical(self, "Error", "Please select a FIT file first")
            return
        if not self._can_load_new_file():
            QMessageBox.warning(
                self,
                "Analysis running",
                "Wait for the current analysis to finish before loading a new FIT file."
            )
            return
        try:
            _mark_stage("ui:load_fit:start")
            self.file_status.clear()
            self.file_status.append("Loading FIT file...\n")
            QApplication.processEvents()

            # Save current parameters from UI (including checkbox state) BEFORE loading
            self._save_parameters()

            # Hard reset all previous data so reload starts from a clean state.
            uncleared = self._clear_all_loaded_data_for_reload()
            if uncleared:
                self.file_status.append(
                    "Reload cleanup warning: not cleared -> " + ", ".join(uncleared) + "\n"
                )
            else:
                self.file_status.append(
                    "Reload cleanup: all previous segment/power/speed/FIT/elevation/weather data cleared\n"
                )

            use_weather_api_on_load = bool(self.load_weather_api_checkbox.isChecked())
            call_open_elevation_on_load = bool(self.load_open_elevation_checkbox.isChecked())
            call_open_meteo_on_load = bool(self.load_open_meteo_checkbox.isChecked())

            self.weather_api_loaded = False
            self.elevation_api_loaded = False
            self.preloaded_weather_samples = []

            fit_parser = FITParser()
            self.ride_data = fit_parser.parse_fit_file(
                self.fit_file_path,
                status_callback=lambda msg: self.file_status.append(msg),
            )

            # Always preserve FIT altitude source columns
            if 'altitude_fit' in self.ride_data.columns:
                self.ride_data['altitude_fit_source'] = self.ride_data['altitude_fit']
            elif 'altitude' in self.ride_data.columns:
                self.ride_data['altitude_fit_source'] = self.ride_data['altitude']

            # Startup fetches can include multiple elevation APIs
            if call_open_elevation_on_load:
                self.file_status.append("Calling Open-Elevation API on file load...")
                self._fetch_store_elevation_source(
                    service=ElevationService(),
                    source_key='open_elevation',
                    status_callback=lambda msg: self.file_status.append(msg),
                )

            if call_open_meteo_on_load:
                self.file_status.append("Calling Open-Meteo Elevation API on file load...")
                self._fetch_store_elevation_source(
                    service=OpenMeteoElevationService(),
                    source_key='open_meteo',
                    status_callback=lambda msg: self.file_status.append(msg),
                )

            # Determine if at least one API source was fetched
            has_open_elevation = 'altitude_open_elevation' in self.ride_data.columns and self.ride_data['altitude_open_elevation'].notna().any()
            has_open_meteo = 'altitude_open_meteo' in self.ride_data.columns and self.ride_data['altitude_open_meteo'].notna().any()
            self.elevation_api_loaded = bool(has_open_elevation or has_open_meteo)

            # Ensure source-specific slope columns exist
            self._ensure_source_slopes()

            elev_source = 'FIT file'
            
            self.file_status.append(f"Successfully loaded {len(self.ride_data)} data points\n")
            self.file_status.append(
                "Elevation load summary: "
                f"Open-Elevation={'yes' if has_open_elevation else 'no'}, "
                f"Open-Meteo={'yes' if has_open_meteo else 'no'}, FIT=yes\n"
            )

            if use_weather_api_on_load:
                self._prefetch_weather_api_on_load()
            else:
                self.file_status.append("Weather API on load: disabled\n")

            cols = ', '.join(self.ride_data.columns[:10])
            self.file_status.append(f"Columns: {cols}\n")
            if len(self.ride_data.columns) > 10:
                self.file_status.append(f"... and {len(self.ride_data.columns) - 10} more\n")

            self.analyzer = CDAAnalyzer(self.parameters)
            self.analyzer.elevation_source = elev_source
            self.analyzer.preloaded_weather_samples = list(self.preloaded_weather_samples)
            self.analyzer.allow_runtime_weather_fetch = False
            self.weather_service = WeatherService()

            self._sync_api_parameter_checkbox_state()

            self._enable_segment_parameters()
            self._cleanup_results(full_reset=True)
            _mark_stage("ui:load_fit:done")
        except Exception as e:
            _mark_stage("ui:load_fit:exception")
            self.file_status.append(f"Error loading FIT file: {str(e)}\n")
            QMessageBox.critical(self, "Error", str(e))

    def _can_load_new_file(self):
        """Return False when analysis worker is still running.

        Loading a new FIT while background analysis is active can leave Qt objects
        in an inconsistent state and cause hard-to-reproduce crashes.
        """
        return not (self.worker is not None and self.worker.isRunning())

    def _calculate_slope_for_column(self, altitude_col, slope_col):
        """Calculate slope from a specific altitude column and store it."""
        if self.ride_data is None:
            return
        if 'distance' not in self.ride_data.columns or altitude_col not in self.ride_data.columns:
            return

        distance_diff = self.ride_data['distance'].diff()
        altitude_diff = self.ride_data[altitude_col].diff()
        slope_rad = np.where(
            (distance_diff > 0) & (distance_diff.notna()) & (altitude_diff.notna()),
            np.arctan2(altitude_diff, distance_diff),
            0,
        )
        self.ride_data[slope_col] = np.degrees(slope_rad)

    def _ensure_source_slopes(self):
        """Ensure slopes are available for each elevation source column."""
        if self.ride_data is None:
            return

        if 'altitude_fit' in self.ride_data.columns:
            self._calculate_slope_for_column('altitude_fit', 'slope_degrees_fit')
        elif 'altitude' in self.ride_data.columns:
            self.ride_data['altitude_fit'] = self.ride_data['altitude'].copy()
            self._calculate_slope_for_column('altitude_fit', 'slope_degrees_fit')

        if 'altitude_open_elevation' in self.ride_data.columns:
            self._calculate_slope_for_column('altitude_open_elevation', 'slope_degrees_open_elevation')

        if 'altitude_open_meteo' in self.ride_data.columns:
            self._calculate_slope_for_column('altitude_open_meteo', 'slope_degrees_open_meteo')

    def _fetch_store_elevation_source(self, service, source_key, status_callback=None):
        """Fetch one elevation API source and store source-specific columns."""
        if self.ride_data is None:
            return

        df_source, _ = service.apply_to_dataframe(
            self.ride_data.copy(),
            status_callback=status_callback,
        )

        source_col = f'altitude_{source_key}'
        if 'altitude_api' in df_source.columns:
            self.ride_data[source_col] = df_source['altitude_api']

            # Keep legacy field pointing to last fetched API for backward compatibility
            self.ride_data['altitude_api'] = df_source['altitude_api']

    def _clear_all_loaded_data_for_reload(self):
        """Clear all cached ride, segment, analysis, and API-derived state.

        Returns:
            list[str]: Human-readable names of fields that still contained data
            after cleanup (best-effort verification).
        """
        # Data frames/results
        self.ride_data = None
        self.analysis_results = None
        self.preprocessed_segments = None
        self.segment_data_map = {}

        # API preload state
        self.weather_api_loaded = False
        self.elevation_api_loaded = False
        self.preloaded_weather_samples = []

        # Analyzer-side caches/state
        if self.analyzer is not None:
            self.analyzer.weather_cache = {}
            self.analyzer.preloaded_weather_samples = []
            self.analyzer.allow_runtime_weather_fetch = False
            self.analyzer.elevation_source = None

        # Clear shown results to avoid stale UI references after reload.
        self._cleanup_results(full_reset=True)

        uncleared = []
        if self.ride_data is not None:
            uncleared.append("fit_data")
        if self.preprocessed_segments:
            uncleared.append("segments")
        if self.analysis_results:
            uncleared.append("analysis_summary")
        if self.segment_data_map:
            uncleared.append("segment_mapping")
        if self.preloaded_weather_samples:
            uncleared.append("api_weather")
        if self.weather_api_loaded:
            uncleared.append("weather_flag")
        if self.elevation_api_loaded:
            uncleared.append("elevation_flag")
        if self.analyzer is not None:
            if self.analyzer.weather_cache:
                uncleared.append("analyzer_weather_cache")
            if self.analyzer.preloaded_weather_samples:
                uncleared.append("analyzer_preloaded_weather")
            if self.analyzer.elevation_source is not None:
                uncleared.append("analyzer_elevation_source")

        return uncleared

    def _save_parameters(self):
        try:
            for key, entry in self.param_entries.items():
                value = entry.value()
                if key in getattr(self, '_param_percent_keys', set()):
                    value = value / 100.0
                orig = DEFAULT_PARAMETERS[key]
                if isinstance(orig, int):
                    self.parameters[key] = int(round(value))
                else:
                    self.parameters[key] = float(value)
            
            # Handle checkboxes for boolean parameters
            for key, checkbox in self.param_checkboxes.items():
                self.parameters[key] = checkbox.isChecked()

            # Persist file-load weather API checkbox
            if hasattr(self, 'load_weather_api_checkbox'):
                self.load_weather_api_on_file_load = self.load_weather_api_checkbox.isChecked()
            if hasattr(self, 'load_open_elevation_checkbox'):
                self.load_open_elevation_on_file_load = self.load_open_elevation_checkbox.isChecked()
            if hasattr(self, 'load_open_meteo_checkbox'):
                self.load_open_meteo_on_file_load = self.load_open_meteo_checkbox.isChecked()
            
            # Save analysis elevation source from parameters tab radio buttons
            if hasattr(self, 'analysis_elevation_source_group'):
                selected_id = self.analysis_elevation_source_group.checkedId()
                if selected_id == 0:
                    self.parameters['elevation_source'] = 'open_elevation'
                elif selected_id == 1:
                    self.parameters['elevation_source'] = 'open_meteo'
                elif selected_id == 2:
                    self.parameters['elevation_source'] = 'fit_only'
            
            # Update slider if wind_effect_factor changed
            if 'wind_effect_factor' in self.parameters:
                factor_value = self.parameters['wind_effect_factor']
                self.wind_effect_slider.set_value(factor_value, silent=True)
                self.wind_effect_value_label.setText(f"{factor_value:.2f}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Error saving parameters: {str(e)}")

    def _sync_api_parameter_checkbox_state(self):
        """Sync parameter controls based on what data was loaded at file start."""
        weather_key = 'use_weather_api'
        if weather_key in self.param_checkboxes:
            checkbox = self.param_checkboxes[weather_key]
            checkbox.setEnabled(True)
            if self.weather_api_loaded:
                checkbox.setToolTip("Weather API data fetched at file load and available")
            else:
                checkbox.setToolTip("No weather API data fetched at file load; calculation falls back to no wind")

        # Enable/disable analysis elevation source radios based on available loaded columns
        if hasattr(self, 'analysis_open_elevation_radio') and self.ride_data is not None:
            has_open_elevation = bool(
                'altitude_open_elevation' in self.ride_data.columns and
                self.ride_data['altitude_open_elevation'].notna().any()
            )
            has_open_meteo = bool(
                'altitude_open_meteo' in self.ride_data.columns and
                self.ride_data['altitude_open_meteo'].notna().any()
            )
            has_fit = bool(
                'altitude_fit' in self.ride_data.columns and
                self.ride_data['altitude_fit'].notna().any()
            )

            self.analysis_open_elevation_radio.setEnabled(has_open_elevation)
            self.analysis_open_meteo_radio.setEnabled(has_open_meteo)
            self.analysis_fit_radio.setEnabled(has_fit)

            self.analysis_open_elevation_radio.setToolTip(
                "Enabled: Open-Elevation loaded at file start" if has_open_elevation else "Disabled: Open-Elevation not loaded at file start"
            )
            self.analysis_open_meteo_radio.setToolTip(
                "Enabled: Open-Meteo loaded at file start" if has_open_meteo else "Disabled: Open-Meteo not loaded at file start"
            )
            self.analysis_fit_radio.setToolTip(
                "Enabled: FIT elevation is available" if has_fit else "Disabled: FIT elevation unavailable"
            )

            # If selected source is unavailable, fallback to FIT, then available API source
            selected = self.parameters.get('elevation_source', 'open_elevation')
            if selected == 'open_elevation' and not has_open_elevation:
                self.parameters['elevation_source'] = 'fit_only' if has_fit else ('open_meteo' if has_open_meteo else 'open_elevation')
            elif selected == 'open_meteo' and not has_open_meteo:
                self.parameters['elevation_source'] = 'fit_only' if has_fit else ('open_elevation' if has_open_elevation else 'open_meteo')
            elif selected == 'fit_only' and not has_fit:
                self.parameters['elevation_source'] = 'open_elevation' if has_open_elevation else 'open_meteo'

            selected = self.parameters.get('elevation_source', 'open_elevation')
            if selected == 'open_meteo':
                self.analysis_open_meteo_radio.setChecked(True)
            elif selected == 'fit_only':
                self.analysis_fit_radio.setChecked(True)
            else:
                self.analysis_open_elevation_radio.setChecked(True)

    def _prefetch_weather_api_on_load(self):
        """Prefetch weather data for the full route at 3km local-time intervals."""
        self.preloaded_weather_samples = []
        self.weather_api_loaded = False

        if self.ride_data is None:
            return

        sample_distance_m = float(self.parameters.get('weather_sample_distance_m', 3000.0))
        self.file_status.append(f"Weather API: preloading route weather every {sample_distance_m:.0f} m...")
        QApplication.processEvents()

        prefetch = self.weather_service.prefetch_weather_for_ride(
            self.ride_data,
            sample_distance_m=sample_distance_m,
            status_callback=lambda msg: self.file_status.append(msg),
        )
        self.preloaded_weather_samples = prefetch.get('samples', [])
        self.weather_api_loaded = len(self.preloaded_weather_samples) > 0

        self.file_status.append(
            f"Weather API done: samples={prefetch.get('sample_count', 0)}, "
            f"grouped_calls={prefetch.get('grouped_request_count', 0)}"
        )
        QApplication.processEvents()

    def _disable_segment_parameters(self):
        for i, key in enumerate(list(self.parameters.keys())[:8]):
            if key in self.param_entries:
                self.param_entries[key].setEnabled(False)
            if key in self.param_checkboxes:
                self.param_checkboxes[key].setEnabled(False)

        self._sync_api_parameter_checkbox_state()

    def _enable_segment_parameters(self):
        for i, key in enumerate(list(self.parameters.keys())[:8]):
            if key in self.param_entries:
                self.param_entries[key].setEnabled(True)
            if key in self.param_checkboxes:
                self.param_checkboxes[key].setEnabled(True)

        self._sync_api_parameter_checkbox_state()

    def _safe_delete_canvas(self, canvas):
        if canvas is None:
            return
        layout = canvas.parentWidget().layout() if canvas.parentWidget() else None
        if layout:
            layout.removeWidget(canvas)
        canvas.setParent(None)
        canvas.deleteLater()

    def _cleanup_results(self, full_reset=False):
        if hasattr(self, 'summary_status_label'):
            self.summary_status_label.setText("Run analysis to see results here")
            self.summary_status_label.setVisible(True)
        if hasattr(self, '_summary_results_widget'):
            self._summary_results_widget.setVisible(False)

        if self.current_figure:
            # Keep canvas alive and clear figure to avoid draw_idle callbacks
            # targeting a deleted FigureCanvasQTAgg.
            self.current_figure.clear()
            if self.current_canvas:
                self.current_canvas.draw()

        if full_reset:
            self.analysis_results = None
            self.preprocessed_segments = None
            self.segment_data_map = {}

            if self.map_webview:
                self.map_webview.setHtml("<html><body><p>Run analysis to display map</p></body></html>")

    def _run_analysis(self):
        if self.ride_data is None:
            QMessageBox.critical(self, "Error", "Please load a FIT file first")
            return

        _mark_stage("ui:run_analysis:start")

        self._cleanup_results()
        self.summary_status_label.setText("Running analysis…")
        self._save_parameters()
        self.analyzer.update_parameters(self.parameters)
        self.analyzer.preloaded_weather_samples = list(self.preloaded_weather_samples)
        self.analyzer.allow_runtime_weather_fetch = False

        #self.progress.setVisible(True)
        self.progress.setRange(0, 0)  # Indeterminate
        self.analysis_status.setText("Running analysis in background...")

        # Run in thread
        self.worker = WorkerThread(self.analyzer, self.ride_data, self.weather_service)
        self.worker.status.connect(self._on_worker_status)
        self.worker.finished.connect(self._on_analysis_complete)
        self.worker.start()
        _mark_stage("ui:run_analysis:worker_started")

    def _on_worker_status(self, message):
        self.analysis_status.setText(message)
        if self.file_status:
            self.file_status.append(message)

    def _on_analysis_complete(self, results, error, preprocessed_segments):
        try:
            _mark_stage("ui:on_analysis_complete:start")
            #self.progress.setVisible(False)
            self.progress.setRange(0, 100)
            self.progress.setValue(100)
            self.analysis_status.setText("Analysis complete!" if not error else "Analysis failed")

            if error:
                _mark_stage("ui:on_analysis_complete:error")
                self.summary_status_label.setText(f"Analysis failed: {error}")
                self.summary_status_label.setVisible(True)
                QMessageBox.critical(self, "Error", f"Analysis failed: {error}")
                self.analysis_results = None
                self.preprocessed_segments = None
            else:
                _mark_stage("ui:on_analysis_complete:success_path")
                self.analysis_results = results
                self.preprocessed_segments = preprocessed_segments
                self._create_segment_mapping()
                _mark_stage("ui:on_analysis_complete:after_mapping")
                self._display_analysis_results()
                _mark_stage("ui:on_analysis_complete:after_summary")

                # Auto-generate visuals, but isolate failures so UI remains usable.
                self._auto_generate_visuals()

                # Return to summary after auto-generation.
                self.results_notebook.setCurrentWidget(self.summary_frame)
                self.analysis_status.setText("Analysis complete!")
                _mark_stage("ui:on_analysis_complete:done")
        except Exception:
            _mark_stage("ui:on_analysis_complete:exception")
            tb = traceback.format_exc()
            _append_crash_log(f"[UI CALLBACK EXCEPTION]\n{tb}")
            _logger.exception("Unhandled exception in _on_analysis_complete")
            extra = f"\n\nSee log: {_CRASH_LOG_PATH}" if _FILE_LOG_ENABLED else ""
            QMessageBox.critical(self, "Unhandled Error", f"An unexpected UI error occurred.{extra}")

    def _auto_generate_visuals(self):
        """Generate map and plots automatically after analysis.

        Failures are reported and logged per visual type, without aborting the
        analysis result display.
        """
        _mark_stage("ui:auto_visuals:start")

        # Map
        try:
            self.results_notebook.setCurrentWidget(self.map_frame)
            self._generate_map()
            _mark_stage("ui:auto_visuals:map_ok")
        except Exception:
            tb = traceback.format_exc()
            _append_crash_log(f"[AUTO MAP EXCEPTION]\n{tb}")
            _logger.exception("Automatic map generation failed")
            QMessageBox.warning(self, "Map Generation Failed", "Analysis completed, but map generation failed.")
            _mark_stage("ui:auto_visuals:map_fail")

        # Plots
        try:
            self.results_notebook.setCurrentWidget(self.plot_frame)
            self._generate_plots()
            _mark_stage("ui:auto_visuals:plots_ok")
        except Exception:
            tb = traceback.format_exc()
            _append_crash_log(f"[AUTO PLOTS EXCEPTION]\n{tb}")
            _logger.exception("Automatic plot generation failed")
            QMessageBox.warning(self, "Plot Generation Failed", "Analysis completed, but plot generation failed.")
            _mark_stage("ui:auto_visuals:plots_fail")

    def _create_segment_mapping(self):
        if not self.analysis_results or self.ride_data is None:
            return
        self.segment_data_map = {}
        for segment in self.analysis_results['segments']:
            seg_id = segment['segment_id']
            start = segment['start_time']
            end = segment['end_time']
            mask = (self.ride_data['timestamp'] >= start) & (self.ride_data['timestamp'] <= end)
            indices = self.ride_data[mask].index.tolist()
            self.segment_data_map[seg_id] = indices

    def _display_analysis_results(self):
        if not self.analysis_results:
            return
        r = self.analysis_results
        s = r.get('summary') or {}
        ride = s.get('ride_info') or {}

        # ── Metric cards ──────────────────────────────────────────────────
        self.card_cda.update_value(f"{s.get('weighted_cda', 0):.4f}")
        self.card_cda_std.update_value(f"\u00b1{s.get('cda_std', 0):.4f}")

        dist_ridden = ride.get('total_distance_m')
        self.card_dist_ridden.update_value(
            f"{dist_ridden / 1000:.1f} km" if dist_ridden is not None else "\u2014"
        )
        total_analysed = s.get('total_distance')
        self.card_dist_analysed.update_value(
            f"{total_analysed / 1000:.1f} km" if total_analysed is not None else "\u2014"
        )
        vw = s.get('avg_wind_component')
        self.card_vw.update_value(f"{vw:+.2f} m/s" if vw is not None else "\u2014")
        wdir = s.get('avg_wind_direction')
        self.card_wind_dir.update_value(
            f"{wdir:.0f}\u00b0"
            if wdir is not None and not (isinstance(wdir, float) and wdir != wdir)
            else "\u2014"
        )
        np_w = ride.get('normalized_power_w')
        self.card_np.update_value(f"{np_w:.0f} W" if np_w is not None else "\u2014")
        avg_pwr = ride.get('average_power_w')
        self.card_avg_pwr.update_value(f"{avg_pwr:.0f} W" if avg_pwr is not None else "\u2014")

        # ── Details text (collapsible) ────────────────────────────────────
        def _fmt(v, fmt, fallback="N/A"):
            try:
                if v is None or (isinstance(v, float) and v != v):
                    return fallback
                return format(v, fmt)
            except Exception:
                return fallback

        lines = []
        lines.append("Weather Conditions:")
        lines.append(f"  Temperature:      {_fmt(s.get('avg_temp'),           '.1f')} \u00b0C")
        lines.append(f"  Pressure:         {_fmt(s.get('avg_press'),          '.2f')} hPa")
        lines.append(f"  Wind speed:       {_fmt(s.get('avg_wind_speed'),     '.1f')} m/s")
        lines.append(f"  Wind direction:   {_fmt(s.get('avg_wind_direction'), '.1f')} \u00b0")
        lines.append("")
        if ride:
            lines.append("Ride Information:")
            lines.append(f"  Date:             {ride.get('date', 'N/A')}")
            lines.append(f"  Start:            {ride.get('start_time', 'N/A')}")
            lines.append(f"  End:              {ride.get('end_time', 'N/A')}")
            lines.append(
                f"  Duration:         {ride.get('duration_hms', 'N/A')} "
                f"({ride.get('duration_seconds', 'N/A')} s)"
            )
            if dist_ridden is not None:
                lines.append(f"  Distance:         {dist_ridden:.0f} m")
            avg_spd = ride.get('average_speed_kmh')
            if avg_spd is not None:
                lines.append(f"  Avg speed:        {avg_spd:.2f} km/h")
            avg_hr = ride.get('average_heart_rate_bpm')
            if avg_hr is not None:
                lines.append(f"  Avg heart rate:   {avg_hr:.1f} bpm")
            elev_gain = ride.get('elevation_gain_m')
            if elev_gain is not None:
                lines.append(f"  Elevation gain:   {elev_gain:.1f} m")
            lines.append("")
        if s:
            keep_pct  = s.get('keep_percent', self.analyzer.parameters.get('cda_keep_percent', 80.0))
            kept_used = s.get('kept_segments_used', s.get('total_segments', 0))
            lines.append("Analysis Statistics:")
            lines.append(f"  Segments total:   {s.get('total_segments', 0)}")
            lines.append(
                f"  GPS coords:       {'Yes' if s.get('has_gps_coordinates') else 'No'}"
                f"  |  Elev source: {s.get('elevation_source', 'Unknown')}"
            )
            lines.append(f"  Weighted CdA (all):  {s.get('weighted_cda_all', s.get('weighted_cda', 0)):.4f}")
            lines.append(f"  Weighted CdA ({keep_pct:.0f}%): {s.get('weighted_cda_kept', s.get('weighted_cda', 0)):.4f}  [{kept_used} segs]")
            lines.append(f"  Average CdA:      {s.get('average_cda', 0):.4f}")
            lines.append(f"  CdA std dev:      {s.get('cda_std', 0):.4f}")
            if s.get('wind_coefficients'):
                a, b, c = s['wind_coefficients']
                lines.append(f"  Wind formula:     CdA = {a:.2e}\u00b7\u03b8\u00b2 + {b:.2e}\u00b7\u03b8 + {c:.2e}")
            lines.append(f"  v_g (ground):     {s.get('avg_ground_speed', 0):.2f} m/s")
            lines.append(f"  v_w (wind):       {s.get('avg_wind_component', 0):+.2f} m/s  (+head / \u2212tail)")
            lines.append(f"  v_a (air):        {s.get('avg_air_speed', 0):.2f} m/s")
            lines.append(f"  Duration analysed:{s.get('total_duration', 0):.0f} s")
            lines.append(f"  Distance analysed:{s.get('total_distance', 0):.0f} m")
            lines.append("")
            lines.append("Parameters:")
            for k, v in r.get('parameters', {}).items():
                lines.append(f"  {k}: {v}")
        self.details_text.setPlainText("\n".join(lines))

        # ── Segment table ─────────────────────────────────────────────────
        segments = r.get('segments', [])
        has_api = any(seg.get('elevation_api_mean') is not None for seg in segments)
        if has_api:
            headers    = ["ID", "Dur (s)", "Dist (m)", "Elev FIT", "Elev API",
                          "v_g", "v_w", "v_a", "Bearing", "w_angle", "Yaw", "Slope", "Power", "CdA"]
            col_widths = [35, 55, 65, 65, 65, 55, 65, 55, 60, 60, 50, 55, 60, 70]
        else:
            headers    = ["ID", "Dur (s)", "Dist (m)", "Elev FIT",
                          "v_g", "v_w", "v_a", "Bearing", "w_angle", "Yaw", "Slope", "Power", "CdA"]
            col_widths = [35, 55, 65, 65, 55, 65, 55, 60, 60, 50, 55, 60, 70]
        self.segment_table.setColumnCount(len(headers))
        self.segment_table.setHorizontalHeaderLabels(headers)
        for i, w in enumerate(col_widths):
            self.segment_table.setColumnWidth(i, w)
        self.segment_table.setRowCount(len(segments))
        for row, seg in enumerate(segments):
            yaw      = seg.get('yaw', 0.0)
            fit_elev = seg.get('elevation_fit_mean')
            api_elev = seg.get('elevation_api_mean')
            if has_api:
                vals = [
                    str(seg['segment_id']),
                    f"{seg['duration']:.0f}",
                    f"{seg['distance']:.0f}",
                    f"{fit_elev:.0f}" if fit_elev is not None else "N/A",
                    f"{api_elev:.0f}" if api_elev is not None else "N/A",
                    f"{seg.get('v_ground', seg['speed']):.2f}",
                    f"{seg.get('v_wind', seg['effective_wind']):+.2f}",
                    f"{seg.get('v_air', seg['air_speed']):.2f}",
                    f"{seg.get('bearing', 0.0):.0f}\u00b0",
                    f"{seg['wind_angle']:.0f}",
                    f"{yaw:.1f}",
                    f"{seg['slope']:.1f}",
                    f"{seg['power']:.0f}",
                    f"{seg['cda']:.4f}",
                ]
            else:
                vals = [
                    str(seg['segment_id']),
                    f"{seg['duration']:.0f}",
                    f"{seg['distance']:.0f}",
                    f"{fit_elev:.0f}" if fit_elev is not None else "N/A",
                    f"{seg.get('v_ground', seg['speed']):.2f}",
                    f"{seg.get('v_wind', seg['effective_wind']):+.2f}",
                    f"{seg.get('v_air', seg['air_speed']):.2f}",
                    f"{seg.get('bearing', 0.0):.0f}\u00b0",
                    f"{seg['wind_angle']:.0f}",
                    f"{yaw:.1f}",
                    f"{seg['slope']:.1f}",
                    f"{seg['power']:.0f}",
                    f"{seg['cda']:.4f}",
                ]
            for col, v in enumerate(vals):
                item = QTableWidgetItem(v)
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.segment_table.setItem(row, col, item)

        self.summary_status_label.setVisible(False)
        self._summary_results_widget.setVisible(True)

        

    def _generate_segment_colors(self, n_segments):
        base_colors = []
        for cmap_name in ['tab20', 'tab20b', 'tab20c']:
            cmap = plt.colormaps[cmap_name]
            base_colors.extend([cmap(i) for i in range(20)])
        if n_segments <= 60:
            return base_colors[:n_segments]
        colors = []
        rotation_step = 7
        for i in range(n_segments):
            offset = (i // 60) * rotation_step
            idx = (i + offset) % 60
            colors.append(base_colors[idx])
        return colors

    def _generate_map(self):
        if not self.analysis_results or self.ride_data is None:
            QMessageBox.critical(self, "Error", "Please run analysis first")
            return
        try:
            if 'latitude' not in self.ride_data.columns or 'longitude' not in self.ride_data.columns:
                QMessageBox.critical(self, "Error", "No GPS data available in FIT file")
                return
            valid_coords = self.ride_data.dropna(subset=['latitude', 'longitude'])
            if len(valid_coords) == 0:
                QMessageBox.critical(self, "Error", "No valid GPS coordinates found")
                return

            mid_idx = len(valid_coords) // 2
            center_lat = valid_coords.iloc[mid_idx]['latitude']
            center_lon = valid_coords.iloc[mid_idx]['longitude']

            m = folium.Map(location=[center_lat, center_lon], zoom_start=13)
            full_path = list(zip(valid_coords['latitude'], valid_coords['longitude']))
            if len(full_path) > 1:
                folium.PolyLine(full_path, color='gray', weight=2, opacity=0.5, tooltip="Full Ride").add_to(m)

            segments = self.analysis_results['segments']
            if not segments:
                QMessageBox.warning(self, "No Segments", "No steady segments to display.")
                return

            colors = self._generate_segment_colors(len(segments))
            colors_hex = [f"#{int(c[0]*255):02x}{int(c[1]*255):02x}{int(c[2]*255):02x}" for c in colors]

            for i, segment in enumerate(segments):
                seg_id = segment['segment_id']
                if seg_id not in self.segment_data_map:
                    continue
                idx = self.segment_data_map[seg_id]
                if not idx:
                    continue
                data = self.ride_data.iloc[idx].dropna(subset=['latitude', 'longitude'])
                coords = list(zip(data['latitude'], data['longitude']))
                if len(coords) < 2:
                    continue
                color = colors_hex[i]
                popup = (
                    f"<b>Segment {seg_id}</b><br>"
                    f"CdA: {segment['cda']:.4f}<br>"
                    f"Speed: {segment['speed']:.2f} m/s<br>"
                    f"Power: {segment['power']:.0f} W<br>"
                    f"Slope: {segment['slope']:.2f}°"
                )
                folium.PolyLine(coords, color=color, weight=5, opacity=0.9, tooltip=f"Segment {seg_id}",
                                popup=folium.Popup(popup, max_width=250)).add_to(m)
                folium.Marker(
                    location=coords[0],
                    icon=folium.DivIcon(html=f"""
                    <div style="background-color:{color}; border:2px solid white; border-radius:50%; width:24px; height:24px;
                                display:flex; align-items:center; justify-content:center; color:white; font-weight:bold; font-size:12px;">
                    {seg_id}</div>""")
                ).add_to(m)

            # Loading big folium output via setHtml can fail silently in WebEngine.
            # Save to a temp html file and load by URL for robust rendering.
            self._map_html_path = os.path.join(tempfile.gettempdir(), "cda_analyzer_map.html")
            m.save(self._map_html_path)
            self.map_webview.setUrl(QUrl.fromLocalFile(self._map_html_path))

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Error generating map: {str(e)}")

    def _generate_plots(self):
        if not self.analysis_results or self.ride_data is None:
            QMessageBox.critical(self, "Error", "Please run analysis first")
            return
        try:
            segments = self.analysis_results['segments']
            if not segments:
                QMessageBox.warning(self, "No Data", "No steady segments found for plotting.")
                return

            colors = self._generate_segment_colors(len(segments))
            colors_hex = [f"#{int(c[0]*255):02x}{int(c[1]*255):02x}{int(c[2]*255):02x}" for c in colors]

            if self.current_figure is None:
                self.current_figure = Figure(figsize=(16, 10))
            else:
                self.current_figure.clear()
            gs = self.current_figure.add_gridspec(3, 2, hspace=0.45, wspace=0.3)

            cda_vals = [s['cda'] for s in segments]
            air_speeds = [s.get('air_speed', 0) for s in segments]
            seg_ids = [s['segment_id'] for s in segments]
            speeds = [s['speed'] for s in segments]
            powers = [s['power'] for s in segments]
            yaw_vals = [s.get('yaw', 0.0) for s in segments]
            wind_angles = [s.get('wind_angle', 0) for s in segments]

            # --- 1. Speed + Power vs Distance ---
            ax1 = self.current_figure.add_subplot(gs[0, 0])
            ax1.plot(self.ride_data['distance']/1000, self.ride_data['speed'], 'lightgray', alpha=0.5, lw=1, label='Full ride (speed)')
            for i, s in enumerate(segments):
                idx = self.segment_data_map.get(s['segment_id'], [])
                if not idx: continue
                d = self.ride_data.iloc[idx]
                ax1.plot(d['distance']/1000, d['speed'], color=colors[i], lw=2, alpha=0.9, label=f"Seg {s['segment_id']}")
            ax1.set_title('Speed + Power vs Distance', fontsize=10, fontweight='bold')
            ax1.set_xlabel('Distance (km)', fontsize=8)
            ax1.set_ylabel('Speed (m/s)', fontsize=8, color='blue')
            ax1.tick_params(axis='y', labelcolor='blue', labelsize=8)
            ax1.tick_params(axis='x', labelsize=6)
            ax1.grid(True, alpha=0.3)
            if len(segments) <= 10:
                ax1.legend(fontsize=6, loc='upper left')
            ax1_r = ax1.twinx()
            ax1_r.plot(self.ride_data['distance']/1000, self.ride_data['power'], color='orange', alpha=0.5, lw=1)
            for i, s in enumerate(segments):
                idx = self.segment_data_map.get(s['segment_id'], [])
                if not idx: continue
                d = self.ride_data.iloc[idx]
                ax1_r.plot(d['distance']/1000, d['power'], color=colors[i], lw=2.5, alpha=0.8, linestyle='--')
            ax1_r.set_ylabel('Power (W)', fontsize=8, color='red')
            ax1_r.tick_params(axis='y', labelcolor='red', labelsize=8)

            # --- 2. CdA by Segment ---
            ax2 = self.current_figure.add_subplot(gs[0, 1])
            bars = ax2.bar(seg_ids, cda_vals, color=colors, alpha=0.8, edgecolor='k', linewidth=0.7)
            ax2.set_title('CdA by Segment', fontsize=10, fontweight='bold')
            ax2.set_xlabel('Segment ID', fontsize=8)
            ax2.set_ylabel('CdA', fontsize=8)
            ax2.tick_params(axis='x', labelsize=9)
            ax2.grid(True, axis='y', alpha=0.3)
            for bar, cda in zip(bars, cda_vals):
                ax2.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.001,
                         f'{cda:.3f}', ha='center', fontsize=5)

            # --- 3. CdA vs Air Speed ---
            ax3 = self.current_figure.add_subplot(gs[1, 0])
            ax3.scatter(air_speeds, cda_vals, c=colors, s=100, alpha=0.8, edgecolors='k', linewidth=0.5)
            for i, sid in enumerate(seg_ids):
                ax3.annotate(str(sid), (air_speeds[i], cda_vals[i]), xytext=(5, 5), textcoords='offset points', fontsize=6, alpha=0.8)
            ax3.set_title('CdA vs Air Speed', fontsize=10, fontweight='bold')
            ax3.set_xlabel('Air Speed (m/s)', fontsize=8)
            ax3.set_ylabel('CdA', fontsize=8)
            ax3.grid(True, alpha=0.3)

            # --- 4. Speed vs Power ---
            ax4 = self.current_figure.add_subplot(gs[1, 1])
            ax4.scatter(speeds, powers, c=colors, s=100, alpha=0.8, edgecolors='k', linewidth=0.5)
            for i, sid in enumerate(seg_ids):
                ax4.annotate(str(sid), (speeds[i], powers[i]), xytext=(5, 5), textcoords='offset points', fontsize=6, alpha=0.8)
            ax4.set_title('Speed vs Power', fontsize=10, fontweight='bold')
            ax4.set_xlabel('Speed (m/s)', fontsize=8)
            ax4.set_ylabel('Power (W)', fontsize=8)
            ax4.grid(True, alpha=0.3)

            # --- 5. CdA vs Yaw ---
            ax5 = self.current_figure.add_subplot(gs[2, 0])
            sc5 = ax5.scatter(yaw_vals, cda_vals, c=air_speeds, cmap='viridis', s=100, alpha=0.8, edgecolors='k', linewidth=0.5)
            mask_20 = [abs(y) <= 20 for y in yaw_vals]
            yv20 = [yaw_vals[i] for i in range(len(yaw_vals)) if mask_20[i]]
            cv20 = [cda_vals[i] for i in range(len(cda_vals)) if mask_20[i]]
            _yv = np.array(yv20, dtype=float)
            _cv_yaw = np.array(cv20, dtype=float)
            _valid_yaw = np.isfinite(_yv) & np.isfinite(_cv_yaw)
            _yv, _cv_yaw = _yv[_valid_yaw], _cv_yaw[_valid_yaw]
            if len(set(round(float(y), 1) for y in _yv)) >= 5 and np.ptp(_yv) > 0:
                try:
                    with np.errstate(divide='ignore', invalid='ignore'):
                        co = np.polyfit(_yv, _cv_yaw, 4)
                    x_fit = np.linspace(-20, 20, 200)
                    ax5.plot(x_fit, np.poly1d(co)(x_fit), color='red', lw=1.5)
                    ax5.text(0.95, 0.05, f"y={co[0]:.3e}x^4+{co[1]:.3e}x^3+{co[2]:.3e}x^2+{co[3]:.3e}x+{co[4]:.3e}", transform=ax5.transAxes,
                             fontsize=7, color='red', ha='right', va='bottom', bbox=dict(facecolor='white', alpha=0.6))
                except Exception:
                    pass
            for i, sid in enumerate(seg_ids):
                ax5.annotate(str(sid), (yaw_vals[i], cda_vals[i]), xytext=(5, 5), textcoords='offset points', fontsize=6, alpha=0.8)
            ax5.set_title('CdA vs Yaw Angle', fontsize=10, fontweight='bold')
            ax5.set_xlabel('Yaw (\u00b0) \u2014 Crosswind from rider perspective', fontsize=8)
            ax5.set_ylabel('CdA', fontsize=8)
            ax5.set_xlim(-20, 20)
            ax5.set_xticks([-20, -10, 0, 10, 20])
            ax5.grid(True, alpha=0.3)
            self.current_figure.colorbar(sc5, ax=ax5).set_label('Air Speed (m/s)', fontsize=8)

            # --- 6. CdA vs Wind Angle ---
            ax6 = self.current_figure.add_subplot(gs[2, 1])
            sc6 = ax6.scatter(wind_angles, cda_vals, c=air_speeds, cmap='viridis', s=100, alpha=0.8, edgecolors='k', linewidth=0.5)
            _wa = np.array(wind_angles, dtype=float)
            _cv_wa = np.array(cda_vals, dtype=float)
            _valid_wa = np.isfinite(_wa) & np.isfinite(_cv_wa)
            _wa, _cv_wa = _wa[_valid_wa], _cv_wa[_valid_wa]
            if len(set(round(float(w), 1) for w in _wa)) >= 3 and np.ptp(_wa) > 0:
                try:
                    with np.errstate(divide='ignore', invalid='ignore'):
                        co6 = np.polyfit(_wa, _cv_wa, 2)
                    x6 = np.linspace(-180, 180, 300)
                    ax6.plot(x6, np.poly1d(co6)(x6), color='red', lw=1.5)
                    ax6.text(0.98, 0.05, f"y={co6[0]:.3e}x\u00b2+{co6[1]:.3e}x+{co6[2]:.3e}", transform=ax6.transAxes,
                             fontsize=7, color='red', ha='right', va='bottom', bbox=dict(facecolor='white', alpha=0.6))
                except Exception:
                    pass
            for i, sid in enumerate(seg_ids):
                ax6.annotate(str(sid), (wind_angles[i], cda_vals[i]), xytext=(5, 5), textcoords='offset points', fontsize=6, alpha=0.8)
            ax6.set_title('CdA vs Wind Angle', fontsize=10, fontweight='bold')
            ax6.set_xlabel('Wind Angle (\u00b0) — Headwind [\u00b1180\u00b0], Tailwind [0\u00b0]', fontsize=8)
            ax6.set_ylabel('CdA', fontsize=8)
            ax6.set_xlim(-180, 180)
            ax6.set_xticks([-180, -135, -90, -45, 0, 45, 90, 135, 180])
            ax6.grid(True, alpha=0.3)
            self.current_figure.colorbar(sc6, ax=ax6).set_label('Air Speed (m/s)', fontsize=8)

            # Summary text
            weighted_metrics = self.analyzer._calculate_weighted_cda_metrics(segments)
            weighted_kept = weighted_metrics['weighted_cda_kept']
            keep_percent = weighted_metrics['keep_percent']
            std_cda = np.std(cda_vals)
            total_distance = sum(s['distance'] for s in segments) / 1000
            summary = (
                f"Weighted CdA {keep_percent:.0f}%: {weighted_kept:.3f}\n"
                f"CdA Std Dev: {std_cda:.3f}\n"
                f"Total Distance: {total_distance:.1f} km"
            )
            self.current_figure.text(0.45, 0.015, summary, ha='center', va='bottom', fontsize=9, fontweight='bold',
                                     bbox=dict(facecolor='white', edgecolor='black', boxstyle='round,pad=0.5'))

            self.current_figure.suptitle("CDA Analysis Plots", fontsize=12, fontweight='bold', y=0.99)
            self.current_figure.subplots_adjust(top=0.96, bottom=0.08, left=0.05, right=0.98)

            # Create canvas once; then reuse it for future draws.
            if self.current_canvas is None:
                self.current_canvas = FigureCanvas(self.current_figure)
                if self.plot_label and self.plot_label.parent() is not None:
                    self.plot_label.setParent(None)
                if self.plot_button and self.plot_button.parent() is not None:
                    self.plot_button.setParent(None)
                layout = self.plot_frame.layout()
                layout.addWidget(self.current_canvas)

            self.current_canvas.draw()

        except Exception as e:
            self._cleanup_results()
            QMessageBox.critical(self, "Error", f"Error generating plots: {str(e)}")

    def _export_results(self):
        if not self.analysis_results:
            QMessageBox.critical(self, "Error", "No results to export")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Results", "", "JSON files (*.json);;CSV files (*.csv);;All files (*)"
        )
        if not path:
            return
        try:
            if path.endswith('.json'):
                export_data = json.loads(json.dumps(self.analysis_results, default=str))
                with open(path, 'w') as f:
                    json.dump(export_data, f, indent=2)
            elif path.endswith('.csv'):
                df = pd.DataFrame(self.analysis_results['segments'])
                df.to_csv(path, index=False)
            QMessageBox.information(self, "Success", "Results exported successfully.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Export failed: {str(e)}")

    def closeEvent(self, event):
        self._cleanup_results()
        event.accept()

    def _on_wind_effect_slider_moved(self, value):
        """Update the wind effect value label as slider moves"""
        new_factor = value  # SliderRow.valueChanged already emits the real float
        self.wind_effect_value_label.setText(f"{new_factor:.2f}")

    def _on_wind_effect_changed(self):
        """Handle wind effect slider release - re-run analysis with new factor"""
        if not self.analysis_results or self.ride_data is None or not self.preprocessed_segments:
            return

        try:
            # Store the old ride_info before we overwrite analysis_results
            old_ride_info = self.analysis_results.get('summary', {}).get('ride_info')

            # Get the new wind effect factor from slider
            new_factor = self.wind_effect_slider.value()

            # Update analyzer parameter
            self.analyzer.update_parameters({'wind_effect_factor': new_factor})
            self.parameters['wind_effect_factor'] = new_factor
            self.windEffectChanged.emit(new_factor)

            # Re-analyze with progress checkpoints so the bar visibly updates.
            self.progress.setRange(0, 100)
            self.progress.setValue(5)
            self.analysis_status.setText("Re-analyzing with new wind effect factor...")
            QApplication.processEvents()

            # Use the same segments from original analysis
            segment_results = self.analyzer._analyze_segments(self.preprocessed_segments)
            self.progress.setValue(55)
            self.analysis_status.setText("Re-analysis: calculating summary...")
            QApplication.processEvents()

            summary = self.analyzer._calculate_summary(segment_results)

            # RE-INSERT the ride_info back into the new summary
            if old_ride_info:
                summary['ride_info'] = old_ride_info

            # Update results
            self.analysis_results = {
                'segments': segment_results,
                'summary': summary,
                'parameters': self.analyzer.parameters
            }

            self.progress.setValue(70)
            self.analysis_status.setText("Re-analysis: updating summary...")
            QApplication.processEvents()

            # Display updated results
            self._display_analysis_results()

            self.progress.setValue(82)
            self.analysis_status.setText("Re-analysis: refreshing map...")
            QApplication.processEvents()
            self._generate_map()

            self.progress.setValue(92)
            self.analysis_status.setText("Re-analysis: refreshing plots...")
            QApplication.processEvents()
            self._generate_plots()

            self.progress.setValue(100)
            self.analysis_status.setText("Re-analysis complete!")
        except Exception as e:
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
            self.analysis_status.setText("Re-analysis failed")
            QMessageBox.critical(self, "Error", f"Wind effect re-analysis failed: {str(e)}")

    def _set_window_icon(self):
        """Set window icon from logo.PNG"""
        try:
            logo_path = resource_path("icons/logo.PNG")
            self.setWindowIcon(QIcon(str(logo_path)))
        except Exception as e:
            _logger.warning("Could not set icon: %s", e)

def create_splash(app, logo_path, text):
    """Create a splash screen with a box around the logo and text below it."""
    splash = QWidget(flags=Qt.SplashScreen | Qt.FramelessWindowHint)

    # Dark theme to match the app.
    splash.setStyleSheet(
        "QWidget { background-color: #1E1E2E; }"
        "QLabel { color: #E8E8F0; background: transparent; }"
    )

    layout = QVBoxLayout(splash)
    layout.setContentsMargins(20, 20, 20, 20)
    layout.setSpacing(10)
    layout.setAlignment(Qt.AlignCenter)

    # Logo
    pixmap = QPixmap(logo_path)
    if not pixmap.isNull():
        pixmap = pixmap.scaled(300, 300, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        logo_label = QLabel()
        logo_label.setPixmap(pixmap)
        logo_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(logo_label)

    # Text label under logo
    text_label = QLabel(text)
    text_label.setFont(QFont("Arial", 12))
    text_label.setAlignment(Qt.AlignCenter)
    layout.addWidget(text_label)

    # Center splash on screen
    screen = app.desktop().screenGeometry()
    splash.resize(600, 450)
    splash.move((screen.width() - splash.width()) // 2,
                (screen.height() - splash.height()) // 2)
    splash.show()
    app.processEvents()
    return splash

def main(argv=None):
    """Bootstrapped entry point to prevent window flicker."""
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--file-log", action="store_true", help="Enable file-based crash logging")
    parser.add_argument("--log-file", help="Crash log file path (implies --file-log)")
    args, _ = parser.parse_known_args(argv if argv is not None else sys.argv[1:])

    enable_file_log = bool(args.file_log or args.log_file)

    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    QCoreApplication.setAttribute(Qt.AA_DontCreateNativeWidgetSiblings)
    app = QApplication(sys.argv)
    _install_global_error_reporting(
        app,
        enable_file_log=enable_file_log,
        crash_log_path=args.log_file,
    )

    # Use new splash function
    logo_path = resource_path("icons/logo.PNG")
    splash = create_splash(app, logo_path, "Analyzing bike aerodynamics...")

    # Create main window only after splash is shown
    def create_main_window():
        if splash:
            splash.close()
        # Keep a strong reference so the window is not garbage-collected.
        app.main_window = GUIInterface(app)
        app.main_window.show()

    # Delay window creation
    QTimer.singleShot(2500, create_main_window)
    try:
        sys.exit(app.exec_())
    except Exception:
        tb = traceback.format_exc()
        _append_crash_log(f"[APP LOOP EXCEPTION]\n{tb}")
        _logger.exception("Application event loop crashed")
        extra = f"\n\nSee log: {_CRASH_LOG_PATH}" if _FILE_LOG_ENABLED else ""
        _show_fatal_dialog("Fatal Error", f"The application crashed unexpectedly.{extra}")
        raise

if __name__ == "__main__":
    main()