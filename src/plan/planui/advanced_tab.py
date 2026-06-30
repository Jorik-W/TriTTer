"""
Advanced durability tab: inputs and results panel for fatigue-aware pacing.
"""

from PyQt5.QtCore import Qt, QDateTime, pyqtSignal
from PyQt5.QtGui import QColor, QKeySequence
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QFormLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from theme import ACCENT, BG, GREEN, MUTED, ORANGE, RED_COL, SURFACE, TEXT
from planui.widgets import fmt_time
from planui.widgets import MetricCard, SliderRow
from weather_plan import MODE_FORECAST, MODE_HISTORY


class CopyableTableWidget(QTableWidget):
    """QTableWidget with Ctrl+C support for multi-cell TSV copy."""

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.Copy):
            self.copy_selection()
            event.accept()
            return
        super().keyPressEvent(event)

    def copy_selection(self):
        indexes = self.selectedIndexes()
        if not indexes:
            return

        indexes = sorted(indexes, key=lambda idx: (idx.row(), idx.column()))
        min_row = indexes[0].row()
        max_row = indexes[-1].row()
        min_col = min(idx.column() for idx in indexes)
        max_col = max(idx.column() for idx in indexes)

        selected = {(idx.row(), idx.column()): idx.data() for idx in indexes}
        lines = []
        for r in range(min_row, max_row + 1):
            row_vals = []
            for c in range(min_col, max_col + 1):
                row_vals.append(str(selected.get((r, c), "")))
            lines.append("\t".join(row_vals))

        text = "\n".join(lines)
        if text:
            from PyQt5.QtWidgets import QApplication
            QApplication.clipboard().setText(text)


class AdvancedInputPanel(QWidget):
    """Input sliders for advanced durability pacing."""

    weather_changed = pyqtSignal()

    def __init__(self, on_change, on_interaction_finished=None, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._build_conditions_box(layout)

        self.s_ftp = SliderRow("FTP", 150, 450, 309, 1, 0, " W")
        self.s_if = SliderRow("Target IF", 0.50, 1.20, 0.68, 0.01, 2, "")
        self.s_max_power = SliderRow("Max power", 150, 900, 290, 5, 0, " W")
        self.s_reserve = SliderRow("Reserve capacity", 5, 60, 20, 0.5, 1, " kJ")
        self.s_min_reserve = SliderRow("Min reserve warning", 0, 40, 15, 0.5, 1, " kJ")
        self.s_decay = SliderRow("Reserve decay", 0.05, 0.60, 0.20, 0.01, 2, "")

        for s in [
            self.s_ftp,
            self.s_if,
            self.s_max_power,
            self.s_reserve,
            self.s_min_reserve,
            self.s_decay,
        ]:
            layout.addWidget(s)
            s.valueChanged.connect(on_change)
            if on_interaction_finished is not None:
                s.interactionFinished.connect(on_interaction_finished)

        layout.addStretch()

    def _build_conditions_box(self, layout):
        """Weather mode toggle, planned start time and fetch status."""
        self._has_gps = False
        self._has_timestamps = False
        self._fit_start_dt = None
        self._suppress_weather_signal = False

        box = QGroupBox("Conditions (weather)")
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(8, 6, 8, 6)
        box_layout.setSpacing(4)

        form = QFormLayout()
        form.setSpacing(4)
        form.setContentsMargins(0, 0, 0, 0)

        self.cmb_weather_mode = QComboBox()
        self.cmb_weather_mode.addItem("Forecast (future ride)", MODE_FORECAST)
        self.cmb_weather_mode.addItem("History (past ride)", MODE_HISTORY)
        self.cmb_weather_mode.currentIndexChanged.connect(self._on_weather_input_changed)

        self.dt_start = QDateTimeEdit()
        self.dt_start.setCalendarPopup(True)
        self.dt_start.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.dt_start.setDateTime(self._next_top_of_hour())
        self.dt_start.dateTimeChanged.connect(self._on_weather_input_changed)
        self._style_calendar_popup()

        self.chk_use_fit_ts = QCheckBox("Use ride timestamps (from FIT)")
        self.chk_use_fit_ts.setToolTip(
            "History mode only. When on, weather is sampled at the actual FIT "
            "ride times and the start field is locked to the recorded start."
        )
        self.chk_use_fit_ts.toggled.connect(self._on_weather_input_changed)

        self.chk_use_api = QCheckBox("Use live weather (API)")
        self.chk_use_api.setChecked(True)
        self.chk_use_api.setToolTip(
            "On: fetch air density (and route weather) from Open-Meteo.\n"
            "Off: set air density and wind manually with the sliders below."
        )
        self.chk_use_api.toggled.connect(self._on_weather_input_changed)

        # Manual overrides (used only when the API toggle is off).
        self.s_rho = SliderRow("Air density (\u03c1)", 0.95, 1.35, 1.225, 0.005, 3, " kg/m\u00b3")
        self.s_wind_speed = SliderRow("Wind speed", 0.0, 30.0, 0.0, 0.1, 1, " km/h")
        self.s_wind_dir = SliderRow("Wind from", 0, 359, 0, 1, 0, "\u00b0")
        for s in (self.s_rho, self.s_wind_speed, self.s_wind_dir):
            s.valueChanged.connect(self._on_weather_input_changed)

        self.lbl_weather_status = QLabel("Load a GPS course to enable weather")
        self.lbl_weather_status.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self.lbl_weather_status.setWordWrap(True)

        form.addRow("Mode", self.cmb_weather_mode)
        form.addRow("Start", self.dt_start)
        form.addRow("", self.chk_use_fit_ts)
        form.addRow("", self.chk_use_api)
        box_layout.addLayout(form)

        # Sliders in plain VBox so they match every other tab's layout
        for s in (self.s_rho, self.s_wind_speed, self.s_wind_dir):
            box_layout.addWidget(s)

        box_layout.addWidget(self.lbl_weather_status)

        self.weather_box = box
        self.cmb_weather_mode.setEnabled(False)
        self.dt_start.setEnabled(False)
        self.chk_use_fit_ts.setEnabled(False)
        self._sync_weather_widgets()
        layout.addWidget(box)

    def _style_calendar_popup(self):
        """Apply a flat, dark, theme-matched look to the calendar popup."""
        cal = self.dt_start.calendarWidget()
        if cal is None:
            return
        cal.setGridVisible(False)
        cal.setNavigationBarVisible(True)
        cal.setStyleSheet(
            f"""
            QCalendarWidget QWidget {{
                background-color: {BG};
                color: {TEXT};
            }}
            QCalendarWidget QToolButton {{
                background-color: {SURFACE};
                color: {TEXT};
                border: none;
                border-radius: 4px;
                padding: 4px 10px;
                margin: 2px;
                font-size: 12px;
            }}
            QCalendarWidget QToolButton:hover {{
                background-color: {ACCENT};
                color: #ffffff;
            }}
            QCalendarWidget QToolButton::menu-indicator {{
                image: none;
            }}
            QCalendarWidget QMenu {{
                background-color: {SURFACE};
                color: {TEXT};
            }}
            QCalendarWidget QSpinBox {{
                background-color: {SURFACE};
                color: {TEXT};
                border: none;
            }}
            QCalendarWidget QWidget#qt_calendar_navigationbar {{
                background-color: {SURFACE};
            }}
            QCalendarWidget QAbstractItemView {{
                background-color: {BG};
                color: {TEXT};
                selection-background-color: {ACCENT};
                selection-color: #ffffff;
                outline: 0;
                gridline-color: transparent;
            }}
            QCalendarWidget QAbstractItemView:disabled {{
                color: {MUTED};
            }}
            """
        )

    @staticmethod
    def _next_top_of_hour():
        now = QDateTime.currentDateTime()
        now = now.addSecs(3600)
        t = now.time()
        t.setHMS(t.hour(), 0, 0, 0)
        now.setTime(t)
        return now

    def _resolve_use_fit_timestamps(self):
        is_history = self.cmb_weather_mode.currentData() == MODE_HISTORY
        return bool(
            is_history
            and self._has_timestamps
            and self.chk_use_fit_ts.isChecked()
        )

    def _sync_weather_widgets(self):
        """Enable/disable + prefill start field based on mode, FIT data, API toggle."""
        use_api = self.chk_use_api.isChecked()
        is_history = self.cmb_weather_mode.currentData() == MODE_HISTORY
        # API-driven controls are only meaningful while the API toggle is on.
        self.cmb_weather_mode.setEnabled(self._has_gps and use_api)
        self.chk_use_fit_ts.setEnabled(
            self._has_gps and use_api and is_history and self._has_timestamps
        )
        use_fit = use_api and self._resolve_use_fit_timestamps()
        if use_fit and self._fit_start_dt is not None:
            self.dt_start.blockSignals(True)
            self.dt_start.setDateTime(self._fit_start_dt)
            self.dt_start.blockSignals(False)
        # Start field is editable unless locked to FIT ride times.
        self.dt_start.setEnabled(self._has_gps and use_api and not use_fit)
        # Manual overrides are active only when the API toggle is off.
        for s in (self.s_rho, self.s_wind_speed, self.s_wind_dir):
            s.setEnabled(not use_api)

    def _on_weather_input_changed(self, *_args):
        self._sync_weather_widgets()
        if not self._suppress_weather_signal:
            self.weather_changed.emit()

    def set_weather_availability(self, has_gps, has_timestamps, prefer_history=False,
                                 fit_start=None):
        """Enable/disable modes based on what the loaded course provides."""
        self._has_gps = bool(has_gps)
        self._has_timestamps = bool(has_timestamps)
        self._fit_start_dt = (
            QDateTime(fit_start) if fit_start is not None else None
        )
        self._suppress_weather_signal = True

        model = self.cmb_weather_mode.model()
        hist_item = model.item(1)
        hist_item.setEnabled(self._has_gps)

        self.cmb_weather_mode.setEnabled(self._has_gps)

        if self._has_gps:
            if prefer_history and self._has_timestamps:
                self.cmb_weather_mode.setCurrentIndex(1)
        else:
            self.cmb_weather_mode.setCurrentIndex(0)

        # Default the checkbox on when a FIT ride supplies real timestamps.
        self.chk_use_fit_ts.setChecked(self._has_timestamps)

        self._sync_weather_widgets()
        if not self._has_gps:
            self.lbl_weather_status.setText("No GPS in course — using standard air density")
        self._suppress_weather_signal = False

    def set_weather_status(self, text, color=None):
        self.lbl_weather_status.setText(text)
        self.lbl_weather_status.setStyleSheet(
            f"color: {color or MUTED}; font-size: 11px;"
        )

    def get_weather_config(self):
        use_api = self.chk_use_api.isChecked()
        ui_mode = self.cmb_weather_mode.currentData()
        use_fit = self._resolve_use_fit_timestamps()
        # History without FIT timestamps is fetched like a forecast anchored to
        # the manual start time; the archive endpoint is auto-selected by date.
        fetch_mode = MODE_HISTORY if use_fit else MODE_FORECAST
        start_dt = self.dt_start.dateTime().toPyDateTime()
        return {
            'enabled': self._has_gps,
            'use_api': use_api,
            'mode': fetch_mode,
            'ui_mode': ui_mode,
            'use_fit_timestamps': use_fit,
            'start_time': start_dt,
            'has_timestamps': self._has_timestamps,
            'rho': float(self.s_rho.value()),
            'wind_speed_ms': float(self.s_wind_speed.value()) / 3.6,
            'wind_from_deg': float(self.s_wind_dir.value()),
        }

    def get_params(self):
        return {
            'ftp': self.s_ftp.value(),
            'target_if': self.s_if.value(),
            'if_margin': 0.01,
            'max_power_w': self.s_max_power.value(),
            'reserve_j': self.s_reserve.value() * 1000,
            'min_reserve_warn_j': self.s_min_reserve.value() * 1000,
            'reserve_decay_k': self.s_decay.value(),
            'descent_mode': 'race_speed',
        }


class AdvancedResultsPanel(QWidget):
    """Results display for advanced durability model."""

    calculate_clicked = pyqtSignal()
    save_course_clicked = pyqtSignal()
    send_to_gps_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_calculating = False
        self._full_current = False
        self._can_export = False
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(4, 4, 4, 4)

        button_layout = QHBoxLayout()
        button_layout.setSpacing(8)
        self.btn_calculate = QPushButton("Calculate (full resolution)")
        self.btn_calculate.setMaximumWidth(300)
        self.btn_calculate.clicked.connect(self.calculate_clicked.emit)
        button_layout.addWidget(self.btn_calculate)

        self.btn_save_course = QPushButton("Save .FIT power course")
        self.btn_save_course.setObjectName("secondary")
        self.btn_save_course.setToolTip(
            "Export the pacing plan as a Garmin power course (.fit) into the pc folder."
        )
        self.btn_save_course.clicked.connect(self.save_course_clicked.emit)
        button_layout.addWidget(self.btn_save_course)

        self.btn_send_gps = QPushButton("Send to GPS")
        self.btn_send_gps.setObjectName("secondary")
        self.btn_send_gps.setToolTip(
            "Push the power course to an Android device "
            "(/storage/emulated/0/tri-tter/) via ADB."
        )
        self.btn_send_gps.clicked.connect(self.send_to_gps_clicked.emit)
        button_layout.addWidget(self.btn_send_gps)

        button_layout.addStretch()

        self.lbl_export_status = QLabel("")
        self.lbl_export_status.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self.lbl_export_status.setWordWrap(True)
        button_layout.addWidget(self.lbl_export_status, 1)
        layout.addLayout(button_layout)

        metrics_row = QHBoxLayout()
        metrics_row.setSpacing(12)
        self.card_time = MetricCard("Estimated time", "-", accent=True)
        self.card_speed = MetricCard("Avg speed", "-")
        self.card_np = MetricCard("NP", "-")
        self.card_avg_power = MetricCard("Avg power", "-")
        self.card_if = MetricCard("IF", "-")
        self.card_wkg = MetricCard("W/kg", "-")
        self.card_min_reserve = MetricCard("Min reserve", "-")
        self.card_fatigue = MetricCard("Final fatigue", "-")
        self.card_weather_time = MetricCard("Weather time", "-")
        for c in [
            self.card_time,
            self.card_speed,
            self.card_np,
            self.card_avg_power,
            self.card_if,
            self.card_wkg,
            self.card_min_reserve,
            self.card_fatigue,
            self.card_weather_time,
        ]:
            metrics_row.addWidget(c)
        layout.addLayout(metrics_row)

        self.section_box = QGroupBox("Advanced power sections")
        self.section_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._is_downsampled = False
        section_layout = QVBoxLayout(self.section_box)
        section_layout.setSpacing(2)
        section_layout.setContentsMargins(10, 10, 10, 6)

        self.section_table = CopyableTableWidget(0, 13)
        self.section_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.section_table.setMinimumHeight(220)
        self.section_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.section_table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.section_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.section_table.setAlternatingRowColors(True)
        self.section_table.setShowGrid(False)
        self.section_table.verticalHeader().setVisible(False)
        self.section_table.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.section_table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft)
        self.section_table.horizontalHeader().setHighlightSections(False)
        self.section_table.horizontalHeader().setMinimumHeight(22)
        self.section_table.horizontalHeader().setStyleSheet(
            f"QHeaderView::section {{ background: transparent; color: {MUTED}; border: 0; padding: 2px 4px; font-size: 10px; }}"
        )
        self.section_table.setStyleSheet(
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
            }}
            QTableWidget::item {{
                padding: 4px 6px;
            }}
            """
        )
        self.section_table.setHorizontalHeaderLabels([
            "From", "To", "Dist", "Grade", "Power", "Cap", "Thr", "%FTP", "Speed", "Time", "Reserve", "Fatigue", "Weather",
        ])
        for i, w in enumerate([55, 55, 55, 60, 65, 65, 65, 55, 60, 75, 65, 65, 110]):
            self.section_table.setColumnWidth(i, w)
        section_layout.addWidget(self.section_table)

        layout.addWidget(self.section_box)

        self.no_file_label = QLabel(
            "Load a course file (GPX/FIT) to enable advanced durability optimization"
        )
        self.no_file_label.setStyleSheet(
            f"color: {MUTED}; font-size: 13px; padding: 20px; font-style: italic;"
        )
        self.no_file_label.setWordWrap(True)
        layout.addWidget(self.no_file_label)

        self._set_results_visible(False)
        self._refresh_calculate_button()
        self.set_export_enabled(False)

    def _refresh_calculate_button(self):
        if self._is_calculating:
            self.btn_calculate.setEnabled(False)
            self.btn_calculate.setText("Calculating...")
            return
        if self._full_current:
            self.btn_calculate.setEnabled(False)
            self.btn_calculate.setText("Full analysis up to date")
            return
        self.btn_calculate.setEnabled(True)
        self.btn_calculate.setText("Calculate (full resolution)")

    def _set_results_visible(self, visible):
        self.card_time.setVisible(visible)
        self.card_speed.setVisible(visible)
        self.card_np.setVisible(visible)
        self.card_avg_power.setVisible(visible)
        self.card_if.setVisible(visible)
        self.card_wkg.setVisible(visible)
        self.card_min_reserve.setVisible(visible)
        self.card_fatigue.setVisible(visible)
        self.card_weather_time.setVisible(visible)
        self.section_box.setVisible(visible)
        self.no_file_label.setVisible(not visible)

    def _clear_section_rows(self):
        self.section_table.setRowCount(0)

    def _add_section_row(self, section, ftp, warn_j):
        power = section['power']
        pct_ftp = power / ftp * 100 if ftp > 0 else 0
        min_reserve_kj = section['min_reserve'] / 1000
        weather_w = section.get('weather_w', 0.0)
        weather_kmh = section.get('weather_kmh', 0.0)
        values = [
            f"{section['start_km']:.1f}",
            f"{section['end_km']:.1f}",
            f"{section['dist_km']:.1f}k",
            f"{section['avg_grade'] * 100:.1f}%",
            f"{power:.0f} W",
            f"{section['cap_power']:.0f} W",
            f"{section['threshold_power']:.0f} W",
            f"{pct_ftp:.0f}%" if ftp > 0 else "-",
            f"{section['speed_kmh']:.1f}",
            fmt_time(section['time_s'] / 3600),
            f"{min_reserve_kj:.1f}k",
            f"{section['fatigue_drop_pct']:.1f}%",
            f"{weather_w:+.0f}W ({weather_kmh:+.1f} km/h)",
        ]
        row = self.section_table.rowCount()
        self.section_table.insertRow(row)
        for col, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            color = TEXT
            if col == 7 and value.endswith("%"):
                color = RED_COL if pct_ftp > 105 else (ORANGE if pct_ftp > 100 else TEXT)
            if col == 10 and value.endswith("k"):
                color = RED_COL if section['min_reserve'] < warn_j else GREEN
            if col == 12:
                if weather_w < -1.0:
                    color = GREEN
                elif weather_w > 1.0:
                    color = ORANGE
            item.setForeground(QColor(color))
            self.section_table.setItem(row, col, item)

    def update_results(self, sim_result, ftp, distances=None, downsampled=False):
        if sim_result is None:
            self._set_results_visible(False)
            self._clear_section_rows()
            return

        self._is_downsampled = downsampled
        prefix = "~ " if downsampled else ""
        self.section_box.setTitle(f"{prefix}Advanced power sections")

        self._set_results_visible(True)
        self.card_time.update_value(fmt_time(sim_result['time_h'], with_seconds=True), ACCENT)
        self.card_speed.update_value(f"{sim_result['avg_speed']:.1f} km/h")
        self.card_np.update_value(f"{sim_result['np_power']:.0f} W")
        self.card_avg_power.update_value(f"{sim_result['avg_power']:.0f} W")

        intensity = sim_result.get('achieved_if', sim_result['np_power'] / ftp if ftp > 0 else 0.0)
        if_status = sim_result.get('if_status', 'ok')
        if if_status == 'ok':
            if_color = GREEN
        elif if_status == 'low':
            if_color = ORANGE
        else:
            if_color = RED_COL
        self.card_if.update_value(f"{intensity:.2f}", if_color)

        self.card_wkg.update_value(f"{sim_result['wkg']:.2f} W/kg")

        min_reserve_kj = sim_result['min_reserve'] / 1000
        warn_j = sim_result.get('min_reserve_warn_j', 0.0)
        reserve_color = GREEN if sim_result['min_reserve'] >= warn_j else RED_COL
        self.card_min_reserve.update_value(f"{min_reserve_kj:.1f} kJ", reserve_color)

        self.card_fatigue.update_value(f"{sim_result.get('final_fatigue_drop', 0.0) * 100:.1f}%")
        weather_time_s = sim_result.get('weather_time_s', 0.0)
        wt_str = fmt_time(abs(weather_time_s) / 3600.0, with_seconds=True)
        if weather_time_s > 1.0:
            self.card_weather_time.update_value(f"+{wt_str} lost", RED_COL)
        elif weather_time_s < -1.0:
            self.card_weather_time.update_value(f"-{wt_str} saved", GREEN)
        else:
            self.card_weather_time.update_value(fmt_time(0.0, with_seconds=True))

        # Reserve is now shown on the merged elevation graph (see main window).
        self._clear_section_rows()
        for section in sim_result.get('power_sections', []):
            self._add_section_row(section, ftp, warn_j)

    def show_no_file(self):
        self._set_results_visible(False)
        self._clear_section_rows()
        self.set_export_enabled(False)
        self.set_export_status("")

    def set_calculating(self, calculating):
        self._is_calculating = bool(calculating)
        self._refresh_calculate_button()

    def set_full_current(self, is_current):
        self._full_current = bool(is_current)
        self._refresh_calculate_button()

    def set_export_enabled(self, enabled):
        self._can_export = bool(enabled)
        self.btn_save_course.setEnabled(self._can_export)
        self.btn_send_gps.setEnabled(self._can_export)

    def set_export_status(self, text, color=None):
        self.lbl_export_status.setText(text or "")
        self.lbl_export_status.setStyleSheet(
            f"color: {color or MUTED}; font-size: 11px;"
        )
