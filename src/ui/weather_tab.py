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
    QSizePolicy, QSpacerItem, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QRadioButton, QButtonGroup,
    QPushButton, QPlainTextEdit, QCheckBox, QDateTimeEdit,
    QScrollArea, QFrame,
)
from PyQt5.QtCore import Qt, pyqtSignal, QDateTime

from widgets import SectionHeader, SliderRow
from theme import MUTED, TEXT, ACCENT, SURFACE, BORDER, GREEN, ORANGE, RED_COL, apply_calendar_style


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

    def set_file_start_time(self, dt: QDateTime, has_power: bool = False):
        """Called by the Open File tab when a new file is loaded."""
        self._file_dt = dt
        self._time_file.setEnabled(True)
        self._no_file_warning.setVisible(False)
        if has_power:
            self._time_file.setChecked(True)
        if self._time_file.isChecked():
            self.dt_pick.setDateTime(dt)
            self.dt_pick.setEnabled(False)

    def clear_file_time(self):
        """Called when the loaded file is cleared."""
        self._file_dt = None
        self._time_file.setEnabled(False)
        self._time_manual.setChecked(True)
        self.dt_pick.setDateTime(QDateTime.currentDateTime())
        self.dt_pick.setEnabled(True)
        # Warning only shown when user is on "From file" mode with no file
        self._no_file_warning.setVisible(False)

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
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)
        scroll.setWidget(inner)

        layout.addWidget(SectionHeader(
            "Weather",
            subtitle="Choose manual conditions or fetch weather from Open-Meteo. "
                     "Weather settings can be applied to Analyse and Plan."
        ))

        # ── Source selector ──────────────────────────────────────────
        src_row = QHBoxLayout()
        src_row.setSpacing(12)
        self._src_api    = QRadioButton("From API (Open-Meteo)")
        self._src_manual = QRadioButton("Manual")
        self._src_api.setChecked(True)
        src_grp = QButtonGroup(self)
        src_grp.addButton(self._src_api)
        src_grp.addButton(self._src_manual)
        src_row.addWidget(self._src_api)
        src_row.addWidget(self._src_manual)
        src_row.addStretch()
        layout.addLayout(src_row)

        # ── Manual conditions ────────────────────────────────────────
        self.manual_widget = QWidget()
        self.manual_widget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        mb_layout = QVBoxLayout(self.manual_widget)
        mb_layout.setContentsMargins(0, 4, 0, 4)
        mb_layout.setSpacing(2)
        self.s_temp       = SliderRow("Temperature",      -20.0, 50.0, 20.0, 0.5, 1, " °C",  label_width=160)
        self.s_pressure   = SliderRow("Pressure",         900.0, 1100.0, 1013.25, 0.25, 1, " hPa", label_width=160)
        self.s_humidity   = SliderRow("Humidity",         0.0, 100.0, 60.0, 1.0, 0, " %",   label_width=160)
        self.s_wind_speed = SliderRow("Wind speed",       0.0, 20.0, 0.0, 0.1, 1, " m/s",   label_width=160)
        self.s_wind_dir   = SliderRow("Wind direction",   0.0, 359.0, 0.0, 1.0, 0, " °",    label_width=160)
        for s in [self.s_temp, self.s_pressure, self.s_humidity, self.s_wind_speed, self.s_wind_dir]:
            mb_layout.addWidget(s)
            s.valueChanged.connect(self._on_changed)
        layout.addWidget(self.manual_widget)

        # ── API conditions ───────────────────────────────────────────
        self.api_widget = QWidget()
        self.api_widget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        ab_layout = QVBoxLayout(self.api_widget)
        ab_layout.setContentsMargins(0, 4, 0, 4)
        ab_layout.setSpacing(4)

        time_row = QHBoxLayout()
        time_row.setSpacing(12)
        time_lbl = QLabel("Time:")
        time_lbl.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        time_lbl.setFixedWidth(90)
        self._time_file   = QRadioButton("From file")
        self._time_manual = QRadioButton("Manual")
        self._time_file.setEnabled(False)   # disabled until a file is loaded
        self._time_manual.setChecked(True)
        time_grp = QButtonGroup(self)
        time_grp.addButton(self._time_file)
        time_grp.addButton(self._time_manual)
        time_row.addWidget(time_lbl)
        time_row.addWidget(self._time_file)
        time_row.addWidget(self._time_manual)
        time_row.addStretch()
        ab_layout.addLayout(time_row)

        # Warning shown only when "From file" is selected but no file is loaded
        self._no_file_warning = QLabel("No file loaded — load a FIT file to use file time.")
        self._no_file_warning.setStyleSheet(f"color: {ORANGE}; font-size: 11px;")
        self._no_file_warning.setVisible(False)  # hidden at startup (Manual is default)
        ab_layout.addWidget(self._no_file_warning)

        dt_row = QHBoxLayout()
        dt_row.setSpacing(12)
        dt_spacer = QLabel()
        dt_spacer.setFixedWidth(90)
        dt_row.addWidget(dt_spacer)
        self.dt_pick = QDateTimeEdit(QDateTime.currentDateTime())
        self.dt_pick.setDisplayFormat("yyyy-MM-dd  HH:mm")
        self.dt_pick.setCalendarPopup(True)
        apply_calendar_style(self.dt_pick.calendarWidget())
        self.dt_pick.setEnabled(True)   # manual is default, so start enabled
        dt_row.addWidget(self.dt_pick)
        dt_row.addStretch()
        ab_layout.addLayout(dt_row)

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
        self.api_result_box.setFixedHeight(100)
        self.api_result_box.setStyleSheet(
            f"background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER}; "
            "font-family: Consolas, monospace; font-size: 11px;"
        )
        ab_layout.addWidget(self.api_result_box)
        layout.addWidget(self.api_widget)
        # API is the default source, so manual widget starts hidden
        self.manual_widget.setVisible(False)
        self.api_widget.setVisible(True)

        # ── Divider ───────────────────────────────────────────────────
        div = QFrame()
        div.setFrameShape(QFrame.HLine)
        div.setStyleSheet(f"color: {BORDER}; background: {BORDER}; max-height: 1px;")
        layout.addWidget(div)

        # ── Wind effect factor ────────────────────────────────────────
        wef_row = QHBoxLayout()
        wef_row.setSpacing(12)
        wef_lbl = QLabel("Wind effect:")
        wef_lbl.setStyleSheet(f"color: {TEXT}; font-size: 12px; font-weight: bold;")
        wef_lbl.setFixedWidth(90)
        wef_row.addWidget(wef_lbl)
        self.s_wef = SliderRow(
            "0 = sheltered · 1.0 = full wind · 1.5 = exposed",
            0.00, 1.50, 0.40, 0.01, 2, "", label_width=280)
        self.s_wef.valueChanged.connect(self._on_changed)
        wef_row.addWidget(self.s_wef, 1)
        layout.addLayout(wef_row)

        # ── Applies to ───────────────────────────────────────────────
        applies_row = QHBoxLayout()
        applies_row.setSpacing(12)
        applies_lbl = QLabel("Applies to:")
        applies_lbl.setStyleSheet(f"color: {TEXT}; font-size: 12px; font-weight: bold;")
        applies_lbl.setFixedWidth(90)
        self.cb_analyse = QCheckBox("Analyse")
        self.cb_plan    = QCheckBox("Plan")
        self.cb_analyse.setChecked(True)
        self.cb_plan.setChecked(True)
        applies_row.addWidget(applies_lbl)
        applies_row.addWidget(self.cb_analyse)
        applies_row.addWidget(self.cb_plan)
        applies_row.addStretch()
        layout.addLayout(applies_row)
        self.cb_analyse.toggled.connect(self._on_changed)
        self.cb_plan.toggled.connect(self._on_changed)

        layout.addItem(QSpacerItem(0, 0, QSizePolicy.Fixed, QSizePolicy.Expanding))

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
        self.manual_widget.setVisible(manual)
        self.api_widget.setVisible(not manual)
        self._on_changed()

    def _on_time_mode_changed(self):
        file_time = self._time_file.isChecked()
        if file_time and self._file_dt is not None:
            self.dt_pick.setDateTime(self._file_dt)
            self.dt_pick.setEnabled(False)
        else:
            self.dt_pick.setEnabled(not file_time)
        # Warning is only relevant when "From file" is selected but no file loaded
        self._no_file_warning.setVisible(file_time and self._file_dt is None)
        self._on_changed()

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
            self.fetch_status.setText("")
            self._last_fetch_result = None
            self.api_result_box.setPlainText(f"Error: {error}")
            return

        self._last_fetch_result = result
        samples = result.get("samples", [])
        available = result.get("available", True)

        if not available or not samples:
            self.fetch_status.setText("")
            self.api_result_box.setPlainText(_NO_DATA_LABEL)
            self.weatherChanged.emit(self.get_config())
            return

        self.fetch_status.setText(f"✓ {len(samples)} sample(s)")
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
