"""
TriTTer
- Upload a .FIT/.GPX file to extract course data
- Basic model: avg/NP power split across climb/flat/descent
- Advanced model: durability-aware pacing with reserve simulation
"""

import sys
import os
import math
import re
import shutil
import subprocess
import traceback
import numpy as np
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFileDialog, QInputDialog, QGroupBox, QGridLayout,
    QFrame, QScrollArea, QSplitter, QTabWidget, QGraphicsOpacityEffect,
    QSizePolicy
)
from PyQt5.QtCore import Qt, QTimer, QObject, QThread, pyqtSignal
from PyQt5.QtGui import QFont, QFontDatabase

from physics import estimate_time, RHO, solve_speed
from course import parse_course_file, gradient_band_analysis, downsample_course, route_headwind, GRADIENT_BANDS
from durability_model import optimize_pacing, RHO_STD
from fit_export import write_power_course
from weather_plan import (
    fetch_weather_samples,
    densities_from_samples,
    headwinds_from_samples,
    seed_eta_seconds,
    MODE_FORECAST,
    MODE_HISTORY,
)
from theme import ACCENT, BG, SURFACE, CARD, TEXT, MUTED, GREEN, ORANGE, RED_COL
from widgets import MetricCard, SliderRow
from planui.widgets import ProfileBar, ElevationPlot, fmt_time
from planui.advanced_tab import AdvancedInputPanel, AdvancedResultsPanel


APP_DEBOUNCE_MS = 700
ADV_PREVIEW_TARGET_N = 260
CLASSIC_PREVIEW_TARGET_N = 600
PLOT_PREVIEW_TARGET_N = 1200
ADV_PREVIEW_SEGMENT_MAX_M = 100.0
ADV_FULL_SEGMENT_MAX_M = 100.0

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PC_DIR = os.path.join(os.path.abspath(__file__), "..", "..")
ANDROID_GPS_DIR = "/storage/emulated/0/tri-tter/"

# When frozen (PyInstaller), bundled data lives under sys._MEIPASS.
_BUNDLE_DIR = getattr(sys, "_MEIPASS", APP_DIR)
# TriTTer root (two levels up from src/plan) where platform-tools is bundled.
_TRITTER_ROOT = os.path.abspath(os.path.join(APP_DIR, "..", ".."))
# Bundled adb.exe (+ AdbWinApi.dll / AdbWinUsbApi.dll) may be dropped here.
ADB_BUNDLED_DIRS = [
    os.path.join(_BUNDLE_DIR, "platform-tools"),
    os.path.join(_TRITTER_ROOT, "platform-tools"),
    os.path.join(APP_DIR, "platform-tools"),
]


def resolve_adb_path():
    """Locate adb: bundled platform-tools folder first, then PATH."""
    exe = "adb.exe" if os.name == "nt" else "adb"
    for d in ADB_BUNDLED_DIRS:
        candidate = os.path.join(d, exe)
        if os.path.isfile(candidate):
            return candidate
    return shutil.which("adb")



def downsample_plot_data(data, target_n=PLOT_PREVIEW_TARGET_N):
    distances = data.get('distances') or []
    altitudes = data.get('altitudes') or []
    grades = data.get('grades') or []
    if len(distances) <= target_n or len(altitudes) <= target_n:
        return distances, altitudes, grades

    idx = np.linspace(0, len(distances) - 1, target_n, dtype=int)
    plot_distances = [distances[i] for i in idx]
    plot_altitudes = [altitudes[i] for i in idx]
    grade_idx = np.clip(idx[:-1], 0, max(0, len(grades) - 1))
    plot_grades = [grades[i] for i in grade_idx]
    return plot_distances, plot_altitudes, plot_grades


def preview_fit_data(data, target_n):
    if not data.get('grades') or not data.get('distances'):
        return None
    grades_ds, distances_ds = downsample_course(
        data['grades'], data['distances'], target_n=target_n
    )
    preview = dict(data)
    preview['grades'] = grades_ds
    preview['distances'] = distances_ds
    return preview


def advanced_preview_resolution(total_distance_m):
    """Pick preview detail level by route length (speed-first)."""
    dist_km = max(0.0, float(total_distance_m)) / 1000.0
    if dist_km <= 80.0:
        return 260, 100.0
    if dist_km <= 180.0:
        return 220, 100.0
    return 180, 100.0


class AdvancedCalculationWorker(QObject):
    finished = pyqtSignal(int, object, float, bool)
    failed = pyqtSignal(int, str, bool)

    def __init__(self, request_id, grades, distances, adv_params, params, downsampled, calc_segment_max_m, rho=None, wind=None):
        super().__init__()
        self.request_id = request_id
        self.grades = grades
        self.distances = distances
        self.adv_params = dict(adv_params)
        self.params = dict(params)
        self.downsampled = downsampled
        self.calc_segment_max_m = calc_segment_max_m
        self.rho = rho if rho is not None else RHO_STD
        self.wind = wind if wind is not None else 0.0

    def run(self):
        try:
            sim_result = optimize_pacing(
                grades=self.grades,
                distances=self.distances,
                ftp=self.adv_params['ftp'],
                target_if=self.adv_params['target_if'],
                climb_cda=self.params.get('climb_cda'),
                descent_mode=self.adv_params.get('descent_mode', 'race_speed'),
                max_power_w=self.adv_params['max_power_w'],
                reserve_j=self.adv_params['reserve_j'],
                cda=self.params['cda'],
                mass=self.params['mass_kg'],
                crr=self.params['crr'],
                eff=self.params['drivetrain_eff'],
                v_cap=self.params['desc_speed_cap'] / 3.6,
                min_reserve_warn_j=self.adv_params['min_reserve_warn_j'],
                if_margin=self.adv_params['if_margin'],
                reserve_decay_k=self.adv_params.get('reserve_decay_k', 0.20),
                calc_segment_max_m=self.calc_segment_max_m,
                rho=self.rho,
                wind=self.wind,
                target_segments=self.adv_params.get('target_segments'),
            )
            if sim_result is not None:
                # Compute reference time at constant flat power (FTP × IF everywhere)
                flat_pw_wheel = (self.adv_params['ftp'] * self.adv_params['target_if']
                                 * self.params['drivetrain_eff'])
                grades_arr = np.asarray(self.grades, dtype=float)
                dists_m = np.diff(np.asarray(self.distances, dtype=float))
                n = min(len(grades_arr), len(dists_m))
                cda = self.params['cda']
                mass = self.params['mass_kg']
                crr = self.params['crr']
                v_cap = self.params['desc_speed_cap'] / 3.6
                rho_sc = float(np.mean(self.rho)) if np.ndim(self.rho) > 0 else float(self.rho)
                flat_time_s = 0.0
                for i in range(n):
                    v = solve_speed(flat_pw_wheel, float(grades_arr[i]), cda, mass, crr, rho_sc)
                    if float(grades_arr[i]) < -0.005:
                        v = min(v, v_cap)
                    if v > 0.1:
                        flat_time_s += float(dists_m[i]) / v
                sim_result['flat_time_h'] = flat_time_s / 3600
            self.finished.emit(self.request_id, sim_result, self.adv_params['ftp'], self.downsampled)
        except Exception as exc:
            self.failed.emit(self.request_id, f"{exc}\n{traceback.format_exc()}", self.downsampled)


class ClassicCalculationWorker(QObject):
    finished = pyqtSignal(int, object, object)
    failed = pyqtSignal(int, str)

    def __init__(self, request_id, params, fit_data):
        super().__init__()
        self.request_id = request_id
        self.params = dict(params)
        self.fit_data = fit_data

    def run(self):
        try:
            result = estimate_time(self.params, self.fit_data)
            bands = None
            if self.fit_data and self.fit_data.get('valid'):
                bands = gradient_band_analysis(self.fit_data, self.params)
            self.finished.emit(self.request_id, result, bands)
        except Exception as exc:
            self.failed.emit(self.request_id, f"{exc}\n{traceback.format_exc()}")


class CourseLoadWorker(QObject):
    finished = pyqtSignal(int, str, object, object, object, object)
    failed = pyqtSignal(int, str)

    def __init__(self, request_id, path):
        super().__init__()
        self.request_id = request_id
        self.path = path

    def run(self):
        try:
            data = parse_course_file(self.path)
            if not data.get('valid'):
                self.failed.emit(self.request_id, data.get('error', 'Could not parse course file'))
                return

            adv_target_n, _ = advanced_preview_resolution(data.get('total_distance', 0.0))
            adv_preview = preview_fit_data(data, adv_target_n)
            classic_preview = preview_fit_data(data, CLASSIC_PREVIEW_TARGET_N)
            plot_data = downsample_plot_data(data, PLOT_PREVIEW_TARGET_N)
            self.finished.emit(
                self.request_id, self.path, data,
                adv_preview, classic_preview, plot_data
            )
        except Exception as exc:
            self.failed.emit(self.request_id, f"{exc}\n{traceback.format_exc()}")


class WeatherFetchWorker(QObject):
    """Off-thread Open-Meteo fetch. Returns sparse weather samples to cache."""

    finished = pyqtSignal(int, object, str)
    failed = pyqtSignal(int, str)

    def __init__(self, request_id, geometry, config, seed_params):
        super().__init__()
        self.request_id = request_id
        self.geometry = geometry
        self.config = dict(config)
        self.seed_params = dict(seed_params)

    def run(self):
        try:
            distances = self.geometry['distances']
            latitudes = self.geometry.get('latitudes')
            longitudes = self.geometry.get('longitudes')
            grades = self.geometry.get('grades')
            timestamps = self.geometry.get('timestamps')
            mode = self.config['mode']

            eta_seconds = None
            if mode == MODE_FORECAST and grades is not None:
                eta_seconds = seed_eta_seconds(
                    distances, grades,
                    power_w=self.seed_params['power_w'],
                    cda=self.seed_params['cda'],
                    mass=self.seed_params['mass'],
                    crr=self.seed_params['crr'],
                    eff=self.seed_params['eff'],
                )

            samples = fetch_weather_samples(
                distances, latitudes, longitudes,
                mode=mode,
                start_time=self.config.get('start_time'),
                timestamps=timestamps,
                eta_seconds=eta_seconds,
            )
            if samples is None:
                self.finished.emit(self.request_id, None, "Weather unavailable for this course")
                return

            summary = f"Weather: {len(samples)} sample points (20 km / 1 h grid)"
            self.finished.emit(self.request_id, samples, summary)
        except Exception as exc:
            self.failed.emit(self.request_id, f"{exc}\n{traceback.format_exc()}")


class AdbSendWorker(QObject):
    """Push a course file to an Android device via the ``adb`` binary.

    Uses a bundled or PATH ``adb.exe`` over USB (works with the stock Google
    ADB driver). Runs off the UI thread.
    """

    finished = pyqtSignal(bool, str)

    def __init__(self, local_path, remote_dir, adb_path):
        super().__init__()
        self.local_path = local_path
        self.remote_dir = remote_dir
        self.adb_path = adb_path

    def _adb(self, args):
        return subprocess.run(
            [self.adb_path, *args],
            capture_output=True, text=True, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def run(self):
        try:
            devices = self._adb(["devices"])
            lines = devices.stdout.splitlines()[1:]
            ready = [ln for ln in lines if ln.strip() and ln.strip().endswith("device")]
            if any("unauthorized" in ln for ln in lines):
                self.finished.emit(
                    False, "Device unauthorised \u2014 accept the USB debugging prompt on the phone."
                )
                return
            if not ready:
                self.finished.emit(
                    False, "No ADB device connected (enable USB debugging and plug in)."
                )
                return

            mkdir = self._adb(["shell", "mkdir", "-p", self.remote_dir])
            if mkdir.returncode != 0:
                self.finished.emit(
                    False, f"mkdir failed: {mkdir.stderr.strip() or mkdir.stdout.strip()}"
                )
                return

            remote = self.remote_dir.rstrip("/") + "/" + os.path.basename(self.local_path)
            push = self._adb(["push", self.local_path, remote])
            if push.returncode != 0:
                self.finished.emit(
                    False, f"push failed: {push.stderr.strip() or push.stdout.strip()}"
                )
                return
            self.finished.emit(True, f"Sent to {remote}")
        except subprocess.TimeoutExpired:
            self.finished.emit(False, "ADB timed out (is the device unlocked?).")
        except Exception as exc:
            self.finished.emit(False, str(exc))



class BikeEstimator(QMainWindow):
    windEffectChanged = pyqtSignal(float)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("TriTTer")
        self.setMinimumSize(1050, 780)
        self.fit_data = None
        self.fit_data_ds = None  # Downsampled course cache
        self.fit_data_classic = None  # Smaller course cache for Classic band table
        self.fit_plot_data = None
        self._tabs_syncing = False  # Guard flag to prevent tab sync loops
        self._classic_request_id = 0
        self._adv_request_id = 0
        self._course_request_id = 0
        self._weather_request_id = 0
        self._classic_threads = {}
        self._adv_threads = {}
        self._course_threads = {}
        self._weather_threads = {}
        self._results_opacity_effect = None
        self._advanced_full_current = False
        self._last_sim = None  # Latest pacing result for export
        self._last_sim_ftp = None
        self._course_name = None
        self._last_course_path = None
        self._adb_threads = {}
        self._adb_request_id = 0
        self._weather_wef = 0.40   # wind effect factor, updated by WeatherTab via shell
        self._last_weather_cfg: dict = {}  # last config pushed from shared WeatherTab
        self._course_natural_count = None  # natural section count for current course
        self._debounce_timer = QTimer()
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.timeout.connect(self._on_debounce_timeout)
        self._build_ui()
        QTimer.singleShot(50, self._recalculate)

    def _toggle_settings(self, checked: bool):
        self._settings_widget.setVisible(checked)
        self._settings_toggle.setText(
            "\u25bc  Settings" if checked else "\u25b6  Settings")

    def _recover_ui_from_worker_error(self, message, *, source):
        """Never leave UI in a busy state when background worker paths fail."""
        msg = (message or '').splitlines()[0] if message else 'Unknown error'
        self.advanced_results.set_calculating(False)
        if source == 'course':
            self.fit_status.setText(f"Error: {msg}")
            self.fit_status.setStyleSheet(f"color: {RED_COL}; font-size: 11px;")
        elif source == 'advanced':
            self.fit_status.setText(f"Advanced error: {msg}")
            self.fit_status.setStyleSheet(f"color: {ORANGE}; font-size: 11px;")
        elif source == 'classic':
            self.fit_status.setText(f"Classic error: {msg}")
            self.fit_status.setStyleSheet(f"color: {ORANGE}; font-size: 11px;")

    def _set_results_enabled(self, enabled):
        """Enable/disable and visually dim all result panels while busy."""
        if self._results_opacity_effect is None:
            self._results_opacity_effect = QGraphicsOpacityEffect(self.results_tabs)
            self.results_tabs.setGraphicsEffect(self._results_opacity_effect)

        self.results_tabs.setEnabled(enabled)
        self._results_opacity_effect.setOpacity(1.0 if enabled else 0.45)

    def _set_advanced_full_current(self, is_current):
        self._advanced_full_current = bool(is_current)
        self.advanced_results.set_full_current(self._advanced_full_current)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(4)

        # ── Elevation plot ─────────────────────────────────────────────────
        self.elev_plot = ElevationPlot()
        self.elev_plot.setVisible(False)
        self.elev_plot.setMinimumHeight(120)
        self.elev_plot.setMaximumHeight(160)
        root.addWidget(self.elev_plot)

        # ── File status bar ────────────────────────────────────────────────
        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 2, 0, 4)
        self.fit_status = QLabel("No file loaded \u2014 use the Open File tab to load a course")
        self.fit_status.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self.fit_status.setWordWrap(True)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setObjectName("secondary")
        self.clear_btn.setFixedWidth(70)
        self.clear_btn.clicked.connect(self._clear_fit)
        self.clear_btn.setVisible(False)
        status_row.addWidget(self.fit_status, 1)
        status_row.addWidget(self.clear_btn)
        root.addLayout(status_row)

        # ── Settings toggle ────────────────────────────────────────────────
        self._settings_toggle = QPushButton("\u25b6  Settings")
        self._settings_toggle.setObjectName("secondary")
        self._settings_toggle.setCheckable(True)
        self._settings_toggle.setChecked(False)
        self._settings_toggle.toggled.connect(self._toggle_settings)
        root.addWidget(self._settings_toggle)

        # ── Settings panel (collapsible) — IF + desc speed cap only ──────
        self._settings_widget = QScrollArea()
        self._settings_widget.setWidgetResizable(True)
        self._settings_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._settings_widget.setVisible(False)
        self._settings_widget.setMaximumHeight(170)

        settings_inner = QWidget()
        settings_layout = QVBoxLayout(settings_inner)
        settings_layout.setSpacing(4)
        settings_layout.setContentsMargins(8, 8, 8, 8)
        self._settings_widget.setWidget(settings_inner)
        root.addWidget(self._settings_widget)

        self.s_if   = SliderRow("Target IF",          0.0, 1.15, 0.70, 0.01, 2, "")
        self.s_vcap = SliderRow("Max descent speed",   30,   90,  75,   1,   0, " km/h")
        self.s_wef  = SliderRow("Wind effect factor", 0.0, 1.50, 0.40, 0.01, 2, "")
        self.s_target_segs = SliderRow("Max power segments", 1, 50, 17, 1, 0, "")
        self.s_target_segs.setEnabled(False)  # enabled once a course is loaded
        self._target_segs_user_set = False
        self._target_segs_programmatic = False
        for s in [self.s_if, self.s_vcap, self.s_wef, self.s_target_segs]:
            settings_layout.addWidget(s)
            s.valueChanged.connect(self._on_slider_changed)
            s.interactionFinished.connect(self._on_slider_released)
        self.s_target_segs.valueChanged.connect(self._on_target_segs_changed)
        self.s_wef.interactionFinished.connect(
            lambda: self.windEffectChanged.emit(self.s_wef.value()))

        # ── Ghost sliders: course (set by Open File tab / file load) ─────
        self.s_dist  = SliderRow("Distance",          0,   350, 180, 0.5, 1, " km")
        self.s_elev  = SliderRow("Elevation gain",    0,  5000, 2000, 50, 0, " m")
        self.s_grad  = SliderRow("Climb gradient",    0.0,  15,  3.0, 0.1, 1, "%")
        self.s_dgrad = SliderRow("Descent gradient",  0.0,  15,  3.0, 0.1, 1, "%")

        # ── Ghost sliders: power / rider (set by apply_rider) ────────────
        self.s_avgp      = SliderRow("Flat / avg power",  80,  400, 190, 1,   0, " W")
        self.s_ftp       = SliderRow("FTP",              150,  600, 309, 1,   0, " W")
        self.s_cda       = SliderRow("CdA",          0.18, 0.45, 0.261, 0.001, 3, "")
        self.s_climb_cda = SliderRow("Climbing CdA", 0.18, 0.60, 0.40,  0.001, 3, "")
        self.s_mass      = SliderRow("Total mass",     50,  120,  87.0,  0.5,  1, " kg")
        self.s_crr       = SliderRow("Crr",        0.0020, 0.0080, 0.0030, 0.0001, 4, "")
        self.s_eff       = SliderRow("Drivetrain eff.", 95.0, 100.0, 97.5, 0.1, 1, "%")

        # ── Ghost widget: AdvancedInputPanel (reserve/FTP/max_power + weather) ─
        self.advanced_input = AdvancedInputPanel(
            self._on_slider_changed,
            self._on_slider_released,
        )
        # Weather for plan is driven by apply_weather_from_tab() via app_shell.
        # Do NOT connect advanced_input.weather_changed here — weather_box is hidden.
        self.advanced_input.weather_box.setVisible(False)

        # ── Ghost widget: course info box (not visible; kept for _on_course_loaded) ─
        self.course_box = QGroupBox("Course info (from file)")
        course_info_layout = QGridLayout(self.course_box)
        self.fit_info_labels = {}
        infos = [
            ("distance",    "Distance"),
            ("elevation",   "Elevation gain"),
            ("climb_grad",  "Mean climb grad"),
            ("desc_grad",   "Mean desc grad"),
        ]
        for i, (key, label) in enumerate(infos):
            lbl = QLabel(label + ":")
            lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
            val = QLabel("\u2014")
            val.setStyleSheet(f"color: {TEXT}; font-size: 11px; font-weight: bold;")
            course_info_layout.addWidget(lbl, i // 2, (i % 2) * 2)
            course_info_layout.addWidget(val, i // 2, (i % 2) * 2 + 1)
            self.fit_info_labels[key] = val
        self.course_box.setVisible(False)

        # ── Ghost: profile_bar (updated in classic results) ────────────────
        self.profile_bar = ProfileBar()

        # ── Results tabs ───────────────────────────────────────────────────
        self.results_tabs = QTabWidget()

        # Classic results tab
        classic_results = QWidget()
        classic_results_layout = QVBoxLayout(classic_results)
        classic_results_layout.setSpacing(8)
        classic_results_layout.setContentsMargins(4, 8, 4, 4)

        metrics_row = QHBoxLayout()
        metrics_row.setSpacing(8)
        self.card_time  = MetricCard("Estimated time",  "\u2014", accent=True)
        self.card_speed = MetricCard("Avg speed",        "\u2014")
        self.card_wkg   = MetricCard("W/kg",             "\u2014")
        self.card_np_check   = MetricCard("NP",  "\u2014")
        self.card_avg_check  = MetricCard("Avg power check","\u2014")
        for c in [self.card_time, self.card_speed, self.card_wkg, self.card_np_check, self.card_avg_check]:
            metrics_row.addWidget(c, 1)
        classic_results_layout.addLayout(metrics_row)

        # Segment table
        seg_box = QGroupBox("Segment breakdown")
        seg_layout = QVBoxLayout(seg_box)
        seg_layout.setSpacing(2)
        seg_layout.setContentsMargins(10, 10, 10, 6)

        header = QHBoxLayout()
        for txt, w in [("Segment", 80), ("Distance", 80), ("Speed", 80), ("Time", 100), ("Power", 75)]:
            lbl = QLabel(txt)
            lbl.setFixedWidth(w)
            lbl.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
            header.addWidget(lbl)
        header.addStretch()
        seg_layout.addLayout(header)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color: #3A3A52;")
        seg_layout.addWidget(line)

        self.seg_rows = []
        icons = ["\u2191", "\u2192", "\u2193"]
        colors_seg = [ORANGE, TEXT, GREEN]
        for i, (name, icon) in enumerate(zip(["Climbing", "Flat", "Descent"], icons)):
            row_w = QWidget()
            row_l = QHBoxLayout(row_w)
            row_l.setContentsMargins(0, 2, 0, 2)
            cells = []
            for j, w in enumerate([80, 80, 80, 100, 75]):
                lbl = QLabel("\u2014")
                lbl.setFixedWidth(w)
                lbl.setStyleSheet(f"color: {colors_seg[i] if j == 0 else TEXT}; font-size: 11px;")
                row_l.addWidget(lbl)
                cells.append(lbl)
            row_l.addStretch()
            cells[0].setText(f"{icon} {name}")
            seg_layout.addWidget(row_w)
            self.seg_rows.append(cells)

        classic_results_layout.addWidget(seg_box)

        # Gradient band table (classic, visible only when course file loaded)
        self.grad_box = QGroupBox("Power by gradient band (from course file)")
        grad_layout = QVBoxLayout(self.grad_box)
        grad_layout.setSpacing(2)
        grad_layout.setContentsMargins(10, 10, 10, 6)

        grad_header = QHBoxLayout()
        for txt, w in [("Gradient", 75), ("Distance", 70), ("%", 45), ("Power", 65), ("Speed", 65)]:
            lbl = QLabel(txt)
            lbl.setFixedWidth(w)
            lbl.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
            grad_header.addWidget(lbl)
        grad_header.addStretch()
        grad_layout.addLayout(grad_header)

        grad_line = QFrame()
        grad_line.setFrameShape(QFrame.HLine)
        grad_line.setStyleSheet("color: #3A3A52;")
        grad_layout.addWidget(grad_line)

        self.grad_rows = []
        for _ in GRADIENT_BANDS:
            row_w = QWidget()
            row_l = QHBoxLayout(row_w)
            row_l.setContentsMargins(0, 1, 0, 1)
            cells = []
            for w in [75, 70, 45, 65, 65]:
                lbl = QLabel("\u2014")
                lbl.setFixedWidth(w)
                lbl.setStyleSheet(f"color: {TEXT}; font-size: 11px;")
                row_l.addWidget(lbl)
                cells.append(lbl)
            row_l.addStretch()
            grad_layout.addWidget(row_w)
            self.grad_rows.append(cells)

        self.grad_box.setVisible(False)
        classic_results_layout.addWidget(self.grad_box)
        classic_results_layout.addStretch(1)

        self.results_tabs.addTab(classic_results, "Basic")

        # Advanced results tab
        self.advanced_results = AdvancedResultsPanel()
        self.advanced_results.calculate_clicked.connect(self._recalculate_advanced_full)
        self.advanced_results.save_course_clicked.connect(self._save_power_course)
        self.advanced_results.send_to_gps_clicked.connect(self._send_to_gps_course)
        self.results_tabs.addTab(self.advanced_results, "Advanced")
        self.results_tabs.currentChanged.connect(self._on_results_tab_changed)

        root.addWidget(self.results_tabs, 1)

    def _get_params(self):
        params = {
            'dist_km':          self.s_dist.value(),
            'elev_m':           self.s_elev.value(),
            'avg_power':        self.s_ftp.value() * self.s_if.value(),
            'if_target':        self.s_if.value(),
            'ftp':              self.s_ftp.value(),
            'cda':              self.s_cda.value(),
            'climb_cda':        self.s_climb_cda.value(),
            'mass_kg':          self.s_mass.value(),
            'crr':              self.s_crr.value(),
            'drivetrain_eff':   self.s_eff.value() / 100.0,
            'climb_grad':       self.s_grad.value(),
            'desc_grad':        self.s_dgrad.value(),
            'desc_speed_cap':   self.s_vcap.value(),
            'wind_effect_factor': getattr(self, '_weather_wef', 0.40),
        }
        return params

    def apply_rider(self, rider):
        """Sync the selected rider profile into the Plan inputs."""
        if rider is None:
            return
        ov = rider.to_plan_overrides()
        mapping = {
            's_cda': ov.get('cda'),
            's_climb_cda': ov.get('climbing_cda'),
            's_mass': ov.get('mass'),
            's_crr': ov.get('crr'),
            's_eff': (ov.get('eff') or 0.0) * 100.0,
            's_ftp': ov.get('ftp'),
        }
        for attr, val in mapping.items():
            slider = getattr(self, attr, None)
            if slider is not None and val is not None:
                try:
                    slider.set_value(float(val), silent=True)
                except (TypeError, ValueError):
                    pass
        # Push power/reserve params into AdvancedInputPanel ghost sliders
        adv_map = [
            ('s_ftp',        ov.get('ftp')),
            ('s_max_power',  ov.get('max_power')),
            ('s_reserve',    ov.get('reserve_kj')),
            ('s_min_reserve', ov.get('min_reserve_kj')),
            ('s_decay',      ov.get('reserve_decay')),
        ]
        for attr, val in adv_map:
            slider = getattr(self.advanced_input, attr, None)
            if slider is not None and val is not None:
                try:
                    slider.set_value(float(val), silent=True)
                except (TypeError, ValueError):
                    pass
        self._set_advanced_full_current(False)
        self._recalculate()

    def _apply_rider_silent(self, rider):
        """Push rider values into sliders without triggering recalculation."""
        if rider is None:
            return
        ov = rider.to_plan_overrides()
        mapping = {
            's_cda': ov.get('cda'),
            's_climb_cda': ov.get('climbing_cda'),
            's_mass': ov.get('mass'),
            's_crr': ov.get('crr'),
            's_eff': (ov.get('eff') or 0.0) * 100.0,
            's_ftp': ov.get('ftp'),
        }
        for attr, val in mapping.items():
            slider = getattr(self, attr, None)
            if slider is not None and val is not None:
                try:
                    slider.set_value(float(val), silent=True)
                except (TypeError, ValueError):
                    pass
        adv_map = [
            ('s_ftp',        ov.get('ftp')),
            ('s_max_power',  ov.get('max_power')),
            ('s_reserve',    ov.get('reserve_kj')),
            ('s_min_reserve', ov.get('min_reserve_kj')),
            ('s_decay',      ov.get('reserve_decay')),
        ]
        for attr, val in adv_map:
            slider = getattr(self.advanced_input, attr, None)
            if slider is not None and val is not None:
                try:
                    slider.set_value(float(val), silent=True)
                except (TypeError, ValueError):
                    pass
        self._set_advanced_full_current(False)

    def set_manual_course(self, dist_km: float, elev_m: float, climb_grad: float, desc_grad: float):
        """Called by shell when Open File tab manual course values change (no file loaded)."""
        if self.fit_data is None:
            self.s_dist.set_value(dist_km, silent=True)
            self.s_elev.set_value(elev_m, silent=True)
            self.s_grad.set_value(climb_grad, silent=True)
            self.s_dgrad.set_value(desc_grad, silent=True)
        self._recalculate()

    def _recalculate(self, *_args):
        params = self._get_params()
        self._recalculate_classic(params)
        self._recalculate_advanced(params)

    def _recalculate_classic(self, params):
        fit_data = self.fit_data_classic or self.fit_data
        self._classic_request_id += 1
        request_id = self._classic_request_id
        thread = QThread(self)
        worker = ClassicCalculationWorker(request_id, params, fit_data)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_classic_worker_finished)
        worker.failed.connect(self._on_classic_worker_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda rid=request_id: self._classic_threads.pop(rid, None))
        self._classic_threads[request_id] = (thread, worker)
        thread.start()

    def _on_classic_worker_finished(self, request_id, result, bands):
        try:
            if request_id != self._classic_request_id:
                return

            if result is None:
                for card in [self.card_time, self.card_speed, self.card_wkg]:
                    card.update_value("Error", RED_COL)
                return

            params = self._get_params()

            self.card_time.update_value(fmt_time(result['time_h'], with_seconds=True), ACCENT)
            self.card_speed.update_value(f"{result['avg_speed']:.1f} km/h")
            self.card_wkg.update_value(f"{result['wkg']:.2f} W/kg")

            target_np = params['avg_power'] * params['if_target']
            target_np = params['ftp'] * params['if_target']
            np_diff  = abs(result['np_check'] - target_np)
            avg_diff = abs(result['avg_check'] - params['avg_power'])
            np_col   = GREEN if np_diff  < 2 else (ORANGE if np_diff  < 8 else RED_COL)
            avg_col  = GREEN if avg_diff < 2 else (ORANGE if avg_diff < 8 else RED_COL)
            self.card_np_check.update_value(f"{result['np_check']:.0f} W", np_col)
            self.card_avg_check.update_value(f"{result['avg_check']:.0f} W", avg_col)

            for i, seg in enumerate(result['segments']):
                cells = self.seg_rows[i]
                cells[1].setText(f"{seg['dist_km']:.1f} km")
                cells[2].setText(f"{seg['speed']:.1f} km/h")
                cells[3].setText(fmt_time(seg['time_h']))
                cells[4].setText(f"{seg['power']:.0f} W")

            self.profile_bar.update_profile(
                result['segments'][0]['dist_km'],
                result['segments'][1]['dist_km'],
                result['segments'][2]['dist_km'],
            )

            # Update gradient band table
            if bands:
                self.grad_box.setVisible(True)
                for i, b in enumerate(bands):
                    cells = self.grad_rows[i]
                    cells[0].setText(b['label'])
                    cells[1].setText(f"{b['dist_m']/1000:.1f} km")
                    cells[2].setText(f"{b['pct']:.0f}%")
                    cells[3].setText(f"{b['power']:.0f} W")
                    cells[4].setText(f"{b['speed_kmh']:.1f} km/h")
                    lo = b['lo']
                    if lo < -0.005:
                        cells[0].setStyleSheet(f"color: {GREEN}; font-size: 11px;")
                    elif lo < 0.005:
                        cells[0].setStyleSheet(f"color: {TEXT}; font-size: 11px;")
                    else:
                        cells[0].setStyleSheet(f"color: {ORANGE}; font-size: 11px;")
            else:
                self.grad_box.setVisible(False)
        except Exception as exc:
            self._on_classic_worker_failed(request_id, str(exc))

    def _on_classic_worker_failed(self, request_id, message):
        if request_id != self._classic_request_id:
            return
        for card in [self.card_time, self.card_speed, self.card_wkg]:
            card.update_value("Error", RED_COL)
        self.grad_box.setVisible(False)
        self._recover_ui_from_worker_error(message, source='classic')

    def _recalculate_advanced(self, params):
        if not self.fit_data or not self.fit_data.get('valid'):
            self._set_results_enabled(True)
            self.advanced_results.show_no_file()
            self.elev_plot.set_inspector_data([], None)
            return

        adv_params = self.advanced_input.get_params()
        # Override IF and FTP from the visible Settings sliders (single source of truth).
        adv_params['target_if'] = self.s_if.value()
        adv_params['ftp']       = self.s_ftp.value()
        # target_segments comes from the visible Settings slider on BikeEstimator.
        # On first run for a new course, skip merge so we learn the natural count first.
        if self._course_natural_count is None:
            adv_params['target_segments'] = None
        else:
            adv_params['target_segments'] = int(self.s_target_segs.value())
        if self.fit_data_ds:
            grades = self.fit_data_ds['grades']
            distances = self.fit_data_ds['distances']
            rho = self.fit_data_ds.get('rho')
            wind = self.fit_data_ds.get('wind')
        else:
            grades = self.fit_data['grades']
            distances = self.fit_data['distances']
            rho = self.fit_data.get('rho')
            wind = self.fit_data.get('wind')

        fallback_total_distance = float(distances[-1]) if len(distances) > 0 else 0.0
        _, preview_segment_max_m = advanced_preview_resolution(
            self.fit_data.get('total_distance', fallback_total_distance)
        )

        self._start_advanced_worker(
            grades, distances, adv_params, params,
            downsampled=True,
            calc_segment_max_m=preview_segment_max_m,
            rho=rho,
            wind=wind,
        )

    def _start_advanced_worker(self, grades, distances, adv_params, params, downsampled, calc_segment_max_m, rho=None, wind=None):
        # Apply wind effect factor to scale the headwind array.
        wef = self.s_wef.value()
        if wind is not None:
            wind = np.asarray(wind, dtype=float) * wef if np.ndim(wind) > 0 else float(wind) * wef
        self._set_results_enabled(False)
        self._adv_request_id += 1
        request_id = self._adv_request_id
        thread = QThread(self)
        worker = AdvancedCalculationWorker(
            request_id, grades, distances, adv_params, params,
            downsampled, calc_segment_max_m, rho, wind
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_advanced_worker_finished)
        worker.failed.connect(self._on_advanced_worker_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda rid=request_id: self._adv_threads.pop(rid, None))
        self._adv_threads[request_id] = (thread, worker)
        thread.start()

    def _on_advanced_worker_finished(self, request_id, sim_result, ftp, downsampled):
        try:
            if request_id != self._adv_request_id:
                if not downsampled:
                    self.advanced_results.set_calculating(False)
                return
            self._set_results_enabled(True)
            if sim_result is not None:
                inspect_sim = dict(sim_result)
                inspect_sim['seg_wbal'] = sim_result.get('seg_reserve', [])
                inspect_sim['seg_grade'] = sim_result.get('grades', [])
                self.elev_plot.set_inspector_data(
                    sim_result['distances_m'], inspect_sim,
                    sim_result.get('min_reserve_warn_j', 0.0)
                )
                # Update target-segments slider max only from full analysis, not quick preview.
                if not downsampled:
                    natural = sim_result.get('natural_section_count')
                    if natural and natural > 0:
                        is_first = self._course_natural_count is None
                        self._course_natural_count = natural
                        self._update_target_segs_slider(natural, reset_default=is_first)
            self.advanced_results.update_results(sim_result, ftp, downsampled=downsampled)
            if not downsampled:
                self._set_advanced_full_current(sim_result is not None)
                self.advanced_results.set_calculating(False)
            self._update_export_state(sim_result, ftp)
        except Exception as exc:
            self._on_advanced_worker_failed(request_id, str(exc), downsampled)

    def _on_advanced_worker_failed(self, request_id, message, downsampled):
        if request_id != self._adv_request_id:
            if not downsampled:
                self.advanced_results.set_calculating(False)
            return
        self._set_results_enabled(True)
        self.advanced_results.show_no_file()
        self.elev_plot.set_inspector_data([], None)
        if not downsampled:
            self._set_advanced_full_current(False)
            self.advanced_results.set_calculating(False)
        self._recover_ui_from_worker_error(message, source='advanced')

    def _recalculate_advanced_sync_removed(self):
        """Reserved to keep worker-only advanced calculations local to this class."""
        return

    def _on_target_segs_changed(self, _v):
        if not self._target_segs_programmatic:
            self._target_segs_user_set = True

    def _update_target_segs_slider(self, natural_count, reset_default=False):
        """Update the visible Target segments slider range/value."""
        if natural_count < 1:
            return
        new_max = int(natural_count)
        default_val = max(1, round(new_max * 0.5))
        self.s_target_segs.setEnabled(True)
        self._target_segs_programmatic = True
        if reset_default:
            self.s_target_segs.set_range(1, new_max, new_value=default_val, silent=False)
            self._target_segs_user_set = False
        else:
            clamped = max(1, min(new_max, int(self.s_target_segs.value())))
            self.s_target_segs.set_range(1, new_max, new_value=clamped, silent=True)
        self._target_segs_programmatic = False

    def _on_power_tab_changed(self, index):
        pass  # power_tabs removed

    def _on_results_tab_changed(self, index):
        """Recalculate when user switches results tabs."""
        self._recalculate()

    def _on_debounce_timeout(self):
        """Debounce timeout: run recalculation with downsampled data for Advanced tab."""
        params = self._get_params()
        
        # Quick refresh should keep both tabs in sync.
        self._recalculate_classic(params)
        self._recalculate_advanced(params)

    def _on_slider_changed(self):
        """Slider change: start debounce timer for recalculation."""
        self._set_advanced_full_current(False)
        self._debounce_timer.stop()
        self._debounce_timer.start(APP_DEBOUNCE_MS)

    def _on_slider_released(self, *_args):
        """Refresh immediately when user finishes slider interaction."""
        self._debounce_timer.stop()
        self._on_debounce_timeout()

    def _recalculate_advanced_full(self):
        """Full-resolution advanced calculation (triggered by Calculate button)."""
        if not self.fit_data or not self.fit_data.get('valid'):
            self._set_results_enabled(True)
            self.advanced_results.show_no_file()
            return

        adv_params = self.advanced_input.get_params()
        # Override IF and FTP from the visible Settings sliders (single source of truth).
        adv_params['target_if'] = self.s_if.value()
        adv_params['ftp']       = self.s_ftp.value()
        # Apply segment reduction from slider (None = first run, let engine decide).
        if not self._target_segs_user_set or self._course_natural_count is None:
            adv_params['target_segments'] = None
        else:
            adv_params['target_segments'] = int(self.s_target_segs.value())
        grades = self.fit_data['grades']
        distances = self.fit_data['distances']
        self.advanced_results.set_calculating(True)
        self._start_advanced_worker(
            grades, distances, adv_params, self._get_params(),
            downsampled=False,
            calc_segment_max_m=ADV_FULL_SEGMENT_MAX_M,
            rho=self.fit_data.get('rho'),
            wind=self.fit_data.get('wind'),
        )

    # -- power-course export ------------------------------------------------

    def _update_export_state(self, sim_result, ftp):
        """Cache the latest pacing plan and toggle the export buttons."""
        has_gps = bool(self._export_geometry())
        sections = sim_result.get('power_sections') if sim_result else None
        if sim_result is not None and sections:
            self._last_sim = sim_result
            self._last_sim_ftp = ftp
        else:
            self._last_sim = None
            self._last_sim_ftp = None
        self.advanced_results.set_export_enabled(bool(self._last_sim) and has_gps)

    def _export_geometry(self):
        """Full-resolution route geometry for export, or None if unavailable."""
        data = self.fit_data
        if not data or not data.get('valid'):
            return None
        lats = data.get('latitudes')
        lons = data.get('longitudes')
        dists = data.get('distances')
        alts = data.get('altitudes')
        if not (lats and lons and dists and alts):
            return None
        if min(len(lats), len(lons), len(dists), len(alts)) < 2:
            return None
        return {
            'distances': dists,
            'latitudes': lats,
            'longitudes': lons,
            'altitudes': alts,
        }

    def _course_file_name(self):
        base = self._course_name or "course"
        safe = re.sub(r'[^A-Za-z0-9._-]+', '_', base).strip('_') or "course"
        return f"{safe}_power.fit"

    def _export_ready(self):
        """Validate export inputs. Returns (geometry_dict, error_message)."""
        geometry = self._export_geometry()
        if not geometry:
            return None, "Load a GPS course (FIT/GPX) before exporting."
        if not self._last_sim or not self._last_sim.get('power_sections'):
            return None, "Run the advanced pacing model before exporting."
        return geometry, None

    def _write_power_course_to(self, path):
        """Write the current pacing plan to ``path``. Returns the path."""
        geometry, err = self._export_ready()
        if err:
            raise ValueError(err)
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        write_power_course(
            path,
            self._course_name or "Power course",
            geometry['distances'],
            geometry['latitudes'],
            geometry['longitudes'],
            geometry['altitudes'],
            self._last_sim['power_sections'],
            total_time_s=self._last_sim.get('total_time_s'),
        )
        self._last_course_path = path
        return path

    def _save_power_course(self):
        geometry, err = self._export_ready()
        if err:
            self.advanced_results.set_export_status(err, color=RED_COL)
            return

        os.makedirs(PC_DIR, exist_ok=True)
        default_path = os.path.join(PC_DIR, self._course_file_name())
        path, _ = QFileDialog.getSaveFileName(
            self, "Save power course", default_path,
            "FIT power course (*.fit);;All files (*)"
        )
        if not path:
            return
        if not path.lower().endswith('.fit'):
            path += '.fit'

        try:
            self._write_power_course_to(path)
        except Exception as exc:
            self.advanced_results.set_export_status(f"Save failed: {exc}", color=RED_COL)
            return
        rel = os.path.relpath(path, APP_DIR)
        shown = rel if not rel.startswith('..') else path
        self.advanced_results.set_export_status(f"Saved: {shown}", color=GREEN)

    def _send_to_gps_course(self):
        geometry, err = self._export_ready()
        if err:
            self.advanced_results.set_export_status(err, color=RED_COL)
            return

        name, ok = QInputDialog.getText(
            self, "Send to GPS", "Filename on device:",
            text=self._course_file_name()
        )
        if not ok:
            return
        name = os.path.basename(name.strip())
        if not name:
            self.advanced_results.set_export_status("Filename required", color=RED_COL)
            return
        if not name.lower().endswith('.fit'):
            name += '.fit'

        adb_path = resolve_adb_path()
        if not adb_path:
            self.advanced_results.set_export_status(
                "adb not found \u2014 put adb.exe in a 'platform-tools' folder next to the app, "
                "or install Android platform-tools on PATH.",
                color=RED_COL,
            )
            return

        try:
            local_path = self._write_power_course_to(os.path.join(PC_DIR, name))
        except Exception as exc:
            self.advanced_results.set_export_status(f"Export failed: {exc}", color=RED_COL)
            return

        self.advanced_results.set_export_enabled(False)
        self.advanced_results.set_export_status("Sending via ADB\u2026", color=MUTED)

        self._adb_request_id += 1
        request_id = self._adb_request_id
        thread = QThread(self)
        worker = AdbSendWorker(local_path, ANDROID_GPS_DIR, adb_path)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_adb_send_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda rid=request_id: self._adb_threads.pop(rid, None))
        self._adb_threads[request_id] = (thread, worker)
        thread.start()

    def _on_adb_send_finished(self, ok, message):
        self.advanced_results.set_export_status(
            message, color=(GREEN if ok else RED_COL)
        )
        self.advanced_results.set_export_enabled(bool(self._last_sim))

    # -- weather / air-density wiring ---------------------------------------

    def _on_weather_config_changed(self):
        """Mode toggle or start-time changed: refetch weather off-thread."""
        self._set_advanced_full_current(False)
        # Only triggered by hidden AdvancedInputPanel; no-op now that
        # shared WeatherTab drives plan weather via apply_weather_from_tab().

    def apply_weather_from_tab(self, cfg: dict, recalc=True):
        """Apply weather settings from the shared WeatherTab to plan calculations.

        Called by app_shell whenever WeatherTab emits weatherChanged.
        Also called from _on_course_loaded to re-apply the last stored config.
        """
        self._last_weather_cfg = dict(cfg)
        if not cfg.get('applies_plan', True):
            return

        self._weather_wef = float(cfg.get('wind_effect_factor', 0.40))
        self.s_wef.set_value(self._weather_wef, silent=True)
        source = cfg.get('source', 'manual')

        if source == 'manual':
            # Derive rho from temperature + pressure (ideal gas law, dry air).
            t_k = float(cfg.get('temperature_c', 15.0)) + 273.15
            p_pa = float(cfg.get('pressure_hpa', 1013.25)) * 100.0
            rho = p_pa / (287.058 * t_k)
            wind_ms = float(cfg.get('wind_speed_ms', 0.0))
            wind_dir = float(cfg.get('wind_direction_deg', 0.0))
            if self.fit_data is not None and self.fit_data.get('valid'):
                self._apply_manual_conditions({
                    'rho': rho,
                    'wind_from_deg': wind_dir,
                    'wind_speed_ms': wind_ms,
                    'use_api': False,
                })
            if recalc:
                self._recalculate()

        elif source == 'api':
            api_result = cfg.get('api_result')
            if api_result and api_result.get('weather_samples'):
                # Weather tab already fetched multi-point samples — use them directly.
                if self.fit_data is not None and self.fit_data.get('valid'):
                    self._apply_weather_samples(api_result['weather_samples'])
                if recalc:
                    self._recalculate()
            elif self.fit_data is not None and self.fit_data.get('valid'):
                self._trigger_weather_fetch_tab(cfg)
            else:
                if recalc:
                    self._recalculate()

    def _trigger_weather_fetch_tab(self, tab_cfg: dict):
        """Start an API weather fetch driven by the shared WeatherTab config."""
        import datetime as _dt
        lats = self.fit_data.get('latitudes')
        lons = self.fit_data.get('longitudes')
        has_gps = (lats is not None and lons is not None
                   and len(lats) > 0 and len(lons) > 0)
        if not has_gps:
            self._clear_rho_cache()
            self._recalculate()
            return

        time_mode = tab_cfg.get('api_time_mode', 'file')
        start_time = None
        if time_mode == 'manual':
            qdt = tab_cfg.get('api_datetime')
            if qdt is not None:
                start_time = _dt.datetime(
                    qdt.date().year(), qdt.date().month(), qdt.date().day(),
                    qdt.time().hour(), qdt.time().minute(),
                )
        # 'file' mode fallback: forecast requires a start_time; use now.
        if start_time is None:
            start_time = _dt.datetime.now()

        config = {
            'mode': MODE_FORECAST,
            'start_time': start_time,
            'enabled': True,
            'use_api': True,
        }
        geometry = {
            'distances': self.fit_data['distances'],
            'latitudes': lats,
            'longitudes': lons,
            'grades': self.fit_data['grades'],
            'timestamps': self.fit_data.get('timestamps'),
        }
        p = self._get_params()
        adv = self.advanced_input.get_params()
        seed_params = {
            'power_w': p['avg_power'],  # ftp × if_target from Settings sliders
            'cda': p['cda'],
            'mass': p['mass_kg'],
            'crr': p['crr'],
            'eff': p['drivetrain_eff'],
        }
        self._weather_request_id += 1
        request_id = self._weather_request_id
        thread = QThread(self)
        worker = WeatherFetchWorker(request_id, geometry, config, seed_params)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_weather_fetched)
        worker.failed.connect(self._on_weather_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda rid=request_id: self._weather_threads.pop(rid, None))
        self._weather_threads[request_id] = (thread, worker)
        thread.start()

    def _trigger_weather_fetch(self):
        if not self.fit_data or not self.fit_data.get('valid'):
            return

        config = self.advanced_input.get_weather_config()

        # Manual conditions: apply rho/wind directly, no network fetch.
        if not config.get('use_api'):
            self._apply_manual_conditions(config)
            ws_kmh = config['wind_speed_ms'] * 3.6
            self.advanced_input.set_weather_status(
                f"Manual: \u03c1={config['rho']:.3f} kg/m\u00b3, "
                f"wind {ws_kmh:.1f} km/h @ {config['wind_from_deg']:.0f}\u00b0",
                color=GREEN,
            )
            self._recalculate_advanced(self._get_params())
            return

        lats = self.fit_data.get('latitudes')
        lons = self.fit_data.get('longitudes')
        has_gps = (
            config.get('enabled')
            and lats is not None and lons is not None
            and len(lats) > 0 and len(lons) > 0
        )
        if not has_gps:
            self._clear_rho_cache()
            return

        geometry = {
            'distances': self.fit_data['distances'],
            'latitudes': lats,
            'longitudes': lons,
            'grades': self.fit_data['grades'],
            'timestamps': self.fit_data.get('timestamps'),
        }
        p = self._get_params()
        adv = self.advanced_input.get_params()
        seed_params = {
            'power_w': p['avg_power'],  # ftp × if_target from Settings sliders
            'cda': p['cda'],
            'mass': p['mass_kg'],
            'crr': p['crr'],
            'eff': p['drivetrain_eff'],
        }

        self.advanced_input.set_weather_status("Fetching weather\u2026")
        self._weather_request_id += 1
        request_id = self._weather_request_id
        thread = QThread(self)
        worker = WeatherFetchWorker(request_id, geometry, config, seed_params)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_weather_fetched)
        worker.failed.connect(self._on_weather_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda rid=request_id: self._weather_threads.pop(rid, None))
        self._weather_threads[request_id] = (thread, worker)
        thread.start()

    def _on_weather_fetched(self, request_id, samples, summary):
        if request_id != self._weather_request_id:
            return
        if samples is None:
            self._clear_rho_cache()
            self.advanced_input.set_weather_status(summary, color=ORANGE)
        else:
            self._apply_weather_samples(samples)
            self.advanced_input.set_weather_status(summary, color=GREEN)
        self._recalculate_advanced(self._get_params())

    def _on_weather_failed(self, request_id, message):
        if request_id != self._weather_request_id:
            return
        self._clear_rho_cache()
        self.advanced_input.set_weather_status(
            "Weather fetch failed; using standard density", color=ORANGE
        )
        self._recalculate_advanced(self._get_params())

    def _apply_weather_samples(self, samples):
        lats = self.fit_data.get('latitudes') if self.fit_data else None
        lons = self.fit_data.get('longitudes') if self.fit_data else None
        route_d = self.fit_data.get('distances') if self.fit_data else None
        if self.fit_data is not None:
            self.fit_data['weather_samples'] = samples
            self.fit_data['rho'] = densities_from_samples(
                self.fit_data['distances'], samples
            )
            self.fit_data['wind'] = headwinds_from_samples(
                self.fit_data['distances'], samples, lats, lons, route_d
            )
        if self.fit_data_ds is not None:
            self.fit_data_ds['rho'] = densities_from_samples(
                self.fit_data_ds['distances'], samples
            )
            self.fit_data_ds['wind'] = headwinds_from_samples(
                self.fit_data_ds['distances'], samples, lats, lons, route_d
            )

    def _apply_manual_conditions(self, config):
        """Apply manual air density + wind (API toggle off) to course caches."""
        rho = float(config['rho'])
        wind_from = float(config['wind_from_deg'])
        wind_ms = float(config['wind_speed_ms'])
        lats = self.fit_data.get('latitudes') if self.fit_data else None
        lons = self.fit_data.get('longitudes') if self.fit_data else None
        route_d = self.fit_data.get('distances') if self.fit_data else None
        has_geom = (
            lats is not None and lons is not None
            and len(lats) > 1 and route_d is not None
        )
        for cache in (self.fit_data, self.fit_data_ds):
            if cache is None:
                continue
            cache['rho'] = rho
            cache.pop('weather_samples', None)
            if wind_ms <= 1e-9:
                cache['wind'] = 0.0
            elif has_geom:
                cache['wind'] = route_headwind(
                    cache['distances'], route_d, lats, lons, wind_from, wind_ms
                )
            else:
                cache['wind'] = wind_ms * math.cos(math.radians(wind_from))

    def _clear_rho_cache(self):
        if self.fit_data is not None:
            self.fit_data.pop('rho', None)
            self.fit_data.pop('weather_samples', None)
            self.fit_data.pop('wind', None)
        if self.fit_data_ds is not None:
            self.fit_data_ds.pop('rho', None)
            self.fit_data_ds.pop('wind', None)

    def _load_fit_external(self, path: str):
        """Load a course file pushed from the Open File tab (bypasses the dialog)."""
        if not path:
            return
        self.fit_status.setText("Parsing course file\u2026")
        self.fit_status.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self._set_advanced_full_current(False)
        self._set_results_enabled(False)

        self._course_request_id += 1
        request_id = self._course_request_id
        thread = QThread(self)
        worker = CourseLoadWorker(request_id, path)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_course_loaded)
        worker.failed.connect(self._on_course_load_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda rid=request_id: self._course_threads.pop(rid, None))
        self._course_threads[request_id] = (thread, worker)
        thread.start()

    def _load_fit(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open course file", "",
            "Course files (*.fit *.FIT *.gpx *.GPX);;FIT files (*.fit *.FIT);;GPX files (*.gpx *.GPX)"
        )
        if not path:
            return
        self._load_fit_external(path)

    def _on_course_loaded(self, request_id, path, data, adv_preview, classic_preview, plot_data):
        try:
            if request_id != self._course_request_id:
                return

            self.fit_data = data
            self.fit_data_ds = adv_preview
            self.fit_data_classic = classic_preview
            self.fit_plot_data = plot_data
            self._course_natural_count = None  # reset so slider default is recalculated
            self._target_segs_user_set = False
            self.s_target_segs.setEnabled(False)

            fname = os.path.basename(path)
            self.fit_status.setText(f"Loaded: {fname}")
            self.fit_status.setStyleSheet(f"color: {GREEN}; font-size: 11px;")
            self.clear_btn.setVisible(True)
            self._course_name = os.path.splitext(fname)[0]

            self.fit_info_labels['distance'].setText(f"{data['total_distance']/1000:.1f} km")
            self.fit_info_labels['elevation'].setText(f"{data['total_elevation']:.0f} m")
            self.fit_info_labels['climb_grad'].setText(f"{data['mean_climb_grad']*100:.1f}%")
            self.fit_info_labels['desc_grad'].setText(f"{data['mean_desc_grad']*100:.1f}%")

            self.s_dist.set_value(data['total_distance'] / 1000, silent=True)
            self.s_elev.set_value(data['total_elevation'], silent=True)
            self.s_grad.set_value(data['mean_climb_grad'] * 100, silent=True)
            self.s_dgrad.set_value(data['mean_desc_grad'] * 100, silent=True)

            plot_distances, plot_altitudes, plot_grades = plot_data
            if len(plot_distances) and len(plot_altitudes):
                self.elev_plot.setVisible(True)
                self.elev_plot.update_data(plot_distances, plot_altitudes, plot_grades)

            lats = data.get('latitudes')
            lons = data.get('longitudes')
            has_gps = (
                lats is not None and lons is not None
                and len(lats) > 0 and len(lons) > 0
            )
            has_ts = bool(data.get('has_timestamps'))
            is_fit = path.lower().endswith('.fit')
            fit_start = None
            if has_ts:
                for ts in (data.get('timestamps') or []):
                    if ts is not None:
                        fit_start = ts
                        break
            self.advanced_input.set_weather_availability(
                has_gps, has_ts, prefer_history=(is_fit and has_ts),
                fit_start=fit_start,
            )

            self._set_results_enabled(True)
            self._recalculate()

            if has_gps:
                # Re-apply weather from the shared WeatherTab (if any was set).
                # Falls back to clearing rho cache (standard ISA density, no wind).
                if self._last_weather_cfg:
                    self.apply_weather_from_tab(self._last_weather_cfg)
                else:
                    self._clear_rho_cache()
        except Exception as exc:
            self._on_course_load_failed(request_id, str(exc))

    def _on_course_load_failed(self, request_id, message):
        if request_id != self._course_request_id:
            return
        self._set_results_enabled(True)
        self.fit_status.setText(f"Error: {message}")
        self.fit_status.setStyleSheet(f"color: {RED_COL}; font-size: 11px;")
        self.fit_data = None
        self.fit_data_ds = None
        self.fit_data_classic = None
        self.fit_plot_data = None
        self._recover_ui_from_worker_error(message, source='course')

    def _clear_fit(self):
        self._classic_request_id += 1
        self._adv_request_id += 1
        self._course_request_id += 1
        self.fit_data = None
        self.fit_data_ds = None  # Clear downsampled cache
        self.fit_data_classic = None
        self.fit_plot_data = None
        self._last_sim = None
        self._last_sim_ftp = None
        self._course_name = None
        self._last_course_path = None
        self._course_natural_count = None
        self._target_segs_user_set = False
        self.s_target_segs.setEnabled(False)
        self._set_advanced_full_current(False)
        self.advanced_results.set_export_enabled(False)
        self.advanced_results.set_export_status("")
        self.fit_status.setText("No file loaded \u2014 use the Open File tab to load a course")
        self.fit_status.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self.clear_btn.setVisible(False)
        self.elev_plot.setVisible(False)
        self.elev_plot.set_inspector_data([], None)
        self.grad_box.setVisible(False)

        self._recalculate()


# Public name used by the TriTTer shell.
PlanTab = BikeEstimator


def main():
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    QCoreApplication.setAttribute(Qt.AA_DontCreateNativeWidgetSiblings)
    app = QApplication(sys.argv)
    app.setApplicationName("Bike Loop Time Estimator")

    for font_name in ["Segoe UI", "SF Pro Display", "Helvetica Neue", "Arial"]:
        fid = QFontDatabase.addApplicationFont(font_name)
        if fid != -1:
            break
    app.setFont(QFont("Segoe UI", 10))

    win = BikeEstimator()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
