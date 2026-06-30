"""Weather tab — shared weather/conditions settings for both Analyse and Plan.

Provides:
  - Manual source: sliders for temp, pressure, humidity, wind speed, wind direction.
  - API source (Open-Meteo): time = file-start or manual datetime; Fetch button;
    status/results text; flag when requested time has no weather data.
  - Wind effect factor (wef): single global slider 0.00–1.50 (default 0.40).
  - Applies-to checkboxes: Analyse / Plan.

External API: :meth:`get_config` returns a dict consumed by Analyse and Plan.
The :attr:`weatherChanged` signal fires on every meaningful change.
"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QRadioButton, QButtonGroup,
    QPushButton, QPlainTextEdit, QGroupBox, QCheckBox, QDateTimeEdit,
    QScrollArea, QSizePolicy,
)
from PyQt5.QtCore import Qt, pyqtSignal, QDateTime

from widgets import SliderRow, SectionHeader
from theme import MUTED, TEXT, ACCENT, SURFACE, BORDER, GREEN, ORANGE, RED_COL


_NO_DATA_LABEL = "No weather data available for the selected time"
_STALE_STYLE   = f"color: {ORANGE}; font-size: 11px;"
_OK_STYLE      = f"color: {GREEN};  font-size: 11px;"
_ERR_STYLE     = f"color: {RED_COL}; font-size: 11px;"


class WeatherTab(QWidget):
    """Single shared weather / conditions configuration tab."""

    weatherChanged = pyqtSignal(dict)   # emits get_config() whenever anything changes

    def __init__(self, parent=None):
        super().__init__(parent)
        self._building = False
        self._last_fetch_result = None
        self._build_ui()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_config(self) -> dict:
        """Return the current weather configuration as a dict.

        Keys
        ----
        source          : "manual" | "api"
        applies_analyse : bool
        applies_plan    : bool
        wind_effect_factor : float  (0.00 – 1.50)

        Manual only:
            temperature_c, pressure_hpa, humidity_pct,
            wind_speed_ms, wind_direction_deg

        API only:
            api_time_mode   : "file" | "manual"
            api_datetime    : QDateTime (if mode=="manual")
            api_result      : dict | None  (last successful fetch)
        """
        cfg: dict = {
            "source": "manual" if self._src_manual.isChecked() else "api",
            "applies_analyse": self.cb_analyse.isChecked(),
            "applies_plan":    self.cb_plan.isChecked(),
            "wind_effect_factor": self.s_wef.value(),
        }
        if cfg["source"] == "manual":
            cfg.update({
                "temperature_c":    self.s_temp.value(),
                "pressure_hpa":     self.s_pressure.value(),
                "humidity_pct":     self.s_humidity.value(),
                "wind_speed_ms":    self.s_wind_speed.value(),
                "wind_direction_deg": self.s_wind_dir.value(),
            })
        else:
            cfg.update({
                "api_time_mode": "file" if self._time_file.isChecked() else "manual",
                "api_datetime":  self.dt_pick.dateTime(),
                "api_result":    self._last_fetch_result,
            })
        return cfg

    def set_file_start_time(self, dt: QDateTime):
        """Called by the Open File tab when a new file is loaded."""
        self._file_dt = dt
        if self._time_file.isChecked():
            self._update_api_time_label()

    # ------------------------------------------------------------------
    # Build UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        self._building = True
        self._file_dt = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)

        inner = QWidget()
        inner.setMaximumWidth(750)
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)
        scroll.setWidget(inner)

        # ── Source selector ──────────────────────────────────────────
        layout.addWidget(SectionHeader("Weather source"))

        src_row = QHBoxLayout()
        self._src_manual = QRadioButton("Manual")
        self._src_api    = QRadioButton("From API (Open-Meteo)")
        self._src_manual.setChecked(True)
        src_grp = QButtonGroup(self)
        src_grp.addButton(self._src_manual)
        src_grp.addButton(self._src_api)
        src_row.addWidget(self._src_manual)
        src_row.addWidget(self._src_api)
        src_row.addStretch()
        layout.addLayout(src_row)

        # ── Manual conditions ────────────────────────────────────────
        self.manual_box = QGroupBox("Manual conditions")
        mb_layout = QVBoxLayout(self.manual_box)
        self.s_temp       = SliderRow("Temperature",      -20.0, 50.0, 20.0, 0.5, 1, " °C",  label_width=160)
        self.s_pressure   = SliderRow("Pressure",         900.0, 1100.0, 1013.25, 0.25, 1, " hPa", label_width=160)
        self.s_humidity   = SliderRow("Humidity",         0.0, 100.0, 60.0, 1.0, 0, " %",   label_width=160)
        self.s_wind_speed = SliderRow("Wind speed",       0.0, 20.0, 0.0, 0.1, 1, " m/s",   label_width=160)
        self.s_wind_dir   = SliderRow("Wind direction",   0.0, 359.0, 0.0, 1.0, 0, " °",    label_width=160)
        for s in [self.s_temp, self.s_pressure, self.s_humidity, self.s_wind_speed, self.s_wind_dir]:
            mb_layout.addWidget(s)
            s.valueChanged.connect(self._on_changed)
        layout.addWidget(self.manual_box)

        # ── API conditions ───────────────────────────────────────────
        self.api_box = QGroupBox("API conditions (Open-Meteo)")
        ab_layout = QVBoxLayout(self.api_box)

        time_row = QHBoxLayout()
        time_lbl = QLabel("Time:")
        time_lbl.setStyleSheet(f"color: {MUTED};")
        self._time_file   = QRadioButton("From file's start time")
        self._time_manual = QRadioButton("Pick date / time")
        self._time_file.setChecked(True)
        time_grp = QButtonGroup(self)
        time_grp.addButton(self._time_file)
        time_grp.addButton(self._time_manual)
        time_row.addWidget(time_lbl)
        time_row.addWidget(self._time_file)
        time_row.addWidget(self._time_manual)
        time_row.addStretch()
        ab_layout.addLayout(time_row)

        dt_row = QHBoxLayout()
        self.dt_pick = QDateTimeEdit(QDateTime.currentDateTime())
        self.dt_pick.setDisplayFormat("yyyy-MM-dd  HH:mm")
        self.dt_pick.setCalendarPopup(True)
        self.dt_pick.setEnabled(False)
        dt_row.addWidget(self.dt_pick)
        dt_row.addStretch()
        ab_layout.addLayout(dt_row)

        self.api_time_label = QLabel()
        self.api_time_label.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        ab_layout.addWidget(self.api_time_label)

        fetch_row = QHBoxLayout()
        self.fetch_btn = QPushButton("Fetch weather")
        self.fetch_btn.setFixedWidth(140)
        self.fetch_status = QLabel()
        self.fetch_status.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self.fetch_status.setWordWrap(True)
        fetch_row.addWidget(self.fetch_btn)
        fetch_row.addWidget(self.fetch_status, 1)
        ab_layout.addLayout(fetch_row)

        self.api_result_box = QPlainTextEdit()
        self.api_result_box.setReadOnly(True)
        self.api_result_box.setPlaceholderText("Weather fetch results will appear here…")
        self.api_result_box.setFixedHeight(120)
        self.api_result_box.setStyleSheet(
            f"background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER}; "
            "font-family: Consolas, monospace; font-size: 11px;"
        )
        ab_layout.addWidget(self.api_result_box)
        layout.addWidget(self.api_box)
        self.api_box.setVisible(False)

        # ── Wind effect factor ────────────────────────────────────────
        layout.addWidget(SectionHeader("Wind effect factor",
            subtitle="Scales the API/manual wind speed to effective ground-level wind. "
                     "0 = fully sheltered, 1.0 = full wind, 1.5 = funnelled/exposed."))
        self.s_wef = SliderRow("Wind effect factor", 0.00, 1.50, 0.40, 0.01, 2, "",
                               label_width=160)
        self.s_wef.valueChanged.connect(self._on_changed)
        layout.addWidget(self.s_wef)

        # ── Applies to ───────────────────────────────────────────────
        layout.addWidget(SectionHeader("Applies to"))
        applies_row = QHBoxLayout()
        self.cb_analyse = QCheckBox("Analyse")
        self.cb_plan    = QCheckBox("Plan")
        self.cb_analyse.setChecked(True)
        self.cb_plan.setChecked(True)
        applies_row.addWidget(self.cb_analyse)
        applies_row.addWidget(self.cb_plan)
        applies_row.addStretch()
        layout.addLayout(applies_row)
        self.cb_analyse.toggled.connect(self._on_changed)
        self.cb_plan.toggled.connect(self._on_changed)

        layout.addStretch()

        # ── Connect source/time toggles ───────────────────────────────
        self._src_manual.toggled.connect(self._on_source_changed)
        self._time_file.toggled.connect(self._on_time_mode_changed)
        self._time_manual.toggled.connect(self._on_time_mode_changed)
        self.dt_pick.dateTimeChanged.connect(self._on_changed)
        self.fetch_btn.clicked.connect(self._on_fetch)

        self._building = False

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_source_changed(self):
        manual = self._src_manual.isChecked()
        self.manual_box.setVisible(manual)
        self.api_box.setVisible(not manual)
        self._on_changed()

    def _on_time_mode_changed(self):
        manual_time = self._time_manual.isChecked()
        self.dt_pick.setEnabled(manual_time)
        self._update_api_time_label()
        self._on_changed()

    def _update_api_time_label(self):
        if self._time_file.isChecked():
            if self._file_dt is not None:
                text = f"File start: {self._file_dt.toString('yyyy-MM-dd  HH:mm')}"
            else:
                text = "No file loaded yet — load a FIT file to use file time."
            self.api_time_label.setText(text)
        else:
            self.api_time_label.setText("")

    def _on_changed(self, *_):
        if self._building:
            return
        self.weatherChanged.emit(self.get_config())

    def _on_fetch(self):
        """Trigger an Open-Meteo API fetch using the app shell's file geometry."""
        self.fetch_btn.setEnabled(False)
        self.fetch_status.setText("Fetching…")
        self.fetch_status.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self.api_result_box.setPlainText("")
        # Actual fetch is driven by the shell (which owns the course file / geometry).
        # Emit a special config so the shell can intercept and call fetch.
        cfg = self.get_config()
        cfg["_request_fetch"] = True
        self.weatherChanged.emit(cfg)

    def show_fetch_result(self, result: dict, *, error: str = ""):
        """Called by the app shell after a weather fetch completes or fails."""
        self.fetch_btn.setEnabled(True)
        if error:
            self.fetch_status.setText(error)
            self.fetch_status.setStyleSheet(_ERR_STYLE)
            self._last_fetch_result = None
            self.api_result_box.setPlainText(f"Error: {error}")
            return

        self._last_fetch_result = result
        samples = result.get("samples", [])
        available = result.get("available", True)

        if not available or not samples:
            self.fetch_status.setText(_NO_DATA_LABEL)
            self.fetch_status.setStyleSheet(_STALE_STYLE)
            self.api_result_box.setPlainText(_NO_DATA_LABEL)
            self.weatherChanged.emit(self.get_config())
            return

        self.fetch_status.setText(f"✓ {len(samples)} samples fetched")
        self.fetch_status.setStyleSheet(_OK_STYLE)

        # Build a human-readable summary of first/avg/last samples.
        lines = []
        def _s(k):
            vals = [s.get(k) for s in samples if s.get(k) is not None]
            return f"{min(vals):.1f} – {max(vals):.1f}" if vals else "—"

        lines.append(f"Temperature:    {_s('temperature_2m')} °C")
        lines.append(f"Wind speed:     {_s('wind_speed_10m')} m/s")
        lines.append(f"Wind direction: {_s('wind_direction_10m')} °")
        lines.append(f"Humidity:       {_s('relative_humidity_2m')} %")
        lines.append(f"Pressure:       {_s('surface_pressure')} hPa")
        self.api_result_box.setPlainText("\n".join(lines))
        self.weatherChanged.emit(self.get_config())
