"""TriTTer main window: five top-level tabs.

Tab order: (Open File) | Weather | Profile | Analyse | Plan
Open File tab is added in Phase 3; Weather is added here in Phase 2.
"""

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QTabWidget, QMessageBox,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont

from profiles import ProfileStore
from profile_tab import ProfileTab
from weather_tab import WeatherTab
from open_file_tab import OpenFileTab
from qt_gui import GUIInterface
from plan_gui import PlanTab


class TriTTerWindow(QMainWindow):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setWindowTitle("TriTTer")
        self.resize(1280, 1040)

        self.store = ProfileStore()
        self._syncing = False

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        # About button at the far right of the tab bar.
        about_btn = QPushButton("  About  ")
        about_btn.setObjectName("secondary")
        about_btn.setToolTip("About TriTTer")
        about_btn.clicked.connect(self._show_about)
        self.tabs.setCornerWidget(about_btn, Qt.TopRightCorner)

        # --- Open File tab (Phase 3) ---
        self.open_file_tab = OpenFileTab()
        self.open_file_tab.fileLoaded.connect(self._on_file_loaded)
        self.open_file_tab.fileCleared.connect(self._on_file_cleared)
        self.tabs.addTab(self.open_file_tab, "Open File")

        # --- Weather tab (Phase 2) ---
        self.weather_tab = WeatherTab()
        self.weather_tab.weatherChanged.connect(self._on_weather_changed)
        self.tabs.addTab(self.weather_tab, "Weather")

        # --- Profile tab ---
        self.profile_tab = ProfileTab(self.store)
        self.profile_tab.riderChanged.connect(self._on_profile_rider_changed)
        self.tabs.addTab(self.profile_tab, "Profile")

        # --- Analyse tab ---
        self.analyze_gui = GUIInterface(app)
        self._analyse_page = self._build_analyze_page()
        self.tabs.addTab(self._analyse_page, "Analyse")

        # --- Plan tab ---
        self.plan_gui = PlanTab()
        self._plan_page = self._build_plan_page()
        self.tabs.addTab(self._plan_page, "Plan")

        # Apply the initially selected rider.
        self._apply_rider(self.store.get_selected())

        self.raise_()
        self.activateWindow()

    # ---- Analyze page -------------------------------------------------
    def _build_analyze_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 6, 6, 6)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("Rider:"))
        self.analyze_combo = QComboBox()
        self.analyze_combo.addItems(self.store.names())
        if self.store.selected:
            self.analyze_combo.setCurrentText(self.store.selected)
        self.analyze_combo.currentTextChanged.connect(self._on_analyze_rider_selected)
        bar.addWidget(self.analyze_combo)

        save_cda_btn = QPushButton("Save measured CdA \u2192 Profile")
        save_cda_btn.setToolTip("Write the latest analyzed CdA into the active rider profile.")
        save_cda_btn.clicked.connect(self._save_measured_cda)
        bar.addWidget(save_cda_btn)
        bar.addStretch()
        layout.addLayout(bar)

        layout.addWidget(self.analyze_gui)
        return page

    def _build_plan_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 6, 6, 6)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("Rider:"))
        self.plan_combo = QComboBox()
        self.plan_combo.addItems(self.store.names())
        if self.store.selected:
            self.plan_combo.setCurrentText(self.store.selected)
        self.plan_combo.currentTextChanged.connect(self._on_plan_rider_selected)
        bar.addWidget(self.plan_combo)
        bar.addStretch()
        layout.addLayout(bar)

        layout.addWidget(self.plan_gui)
        return page

    # ---- file loading ------------------------------------------------
    def _on_file_loaded(self, course):
        """A file was picked in the Open File tab — push to Analyse and Plan."""
        path = course.path

        # Update Weather tab with the file's start time.
        if course.start_time is not None:
            from PyQt5.QtCore import QDateTime
            try:
                import datetime
                st = course.start_time
                if hasattr(st, 'replace'):
                    qdt = QDateTime(st.year, st.month, st.day,
                                    st.hour, st.minute, st.second)
                    self.weather_tab.set_file_start_time(qdt)
            except Exception:
                pass

        from fit_loader import capability_check
        caps = capability_check(course)

        # Push to Analyse (only if file has power+speed).
        analyse_idx = self.tabs.indexOf(self._analyse_page)
        if caps.analyse_ok:
            self.tabs.setTabEnabled(analyse_idx, True)
            try:
                self.analyze_gui.load_file(path)
            except Exception:
                pass
        else:
            self.tabs.setTabEnabled(analyse_idx, False)
            try:
                self.analyze_gui._cleanup_results(full_reset=True)
            except Exception:
                pass

        # Push to Plan (uses shared fit_data via plan_gui internal loader).
        plan_idx = self.tabs.indexOf(self._plan_page)
        if caps.plan_ok:
            self.tabs.setTabEnabled(plan_idx, True)
            try:
                self.plan_gui._load_fit_external(path)
            except Exception:
                pass
        else:
            self.tabs.setTabEnabled(plan_idx, False)

    def _on_file_cleared(self):
        """File was cleared — re-enable tabs but clear loaded data."""
        for page in (self._analyse_page, self._plan_page):
            idx = self.tabs.indexOf(page)
            self.tabs.setTabEnabled(idx, True)
        try:
            self.analyze_gui._clear_all_loaded_data_for_reload()
        except Exception:
            pass
        try:
            self.plan_gui._clear_fit()
        except Exception:
            pass

    # ---- weather changes ----------------------------------------------
    def _on_weather_changed(self, cfg: dict):
        if cfg.get("_request_fetch"):
            # Shell handles the actual fetch in Phase 3 (needs file geometry).
            self.weather_tab.show_fetch_result({}, error="Load a file first to fetch weather.")
            return
        wef = cfg.get("wind_effect_factor", 0.40)
        if cfg.get("applies_analyse", True):
            try:
                self.analyze_gui.update_parameters({"wind_effect_factor": wef})
            except Exception:
                pass
        if cfg.get("applies_plan", True):
            try:
                self.plan_gui._weather_wef = wef
                self.plan_gui._recalculate()
            except Exception:
                pass

    # ---- rider sync ---------------------------------------------------
    def _apply_rider(self, rider):
        if rider is None:
            return
        self.analyze_gui.apply_rider(rider)
        try:
            self.plan_gui.apply_rider(rider)
        except Exception:
            pass

    def _on_profile_rider_changed(self, rider):
        if self._syncing or rider is None:
            return
        self._syncing = True
        self.analyze_combo.setCurrentText(rider.name)
        self.plan_combo.setCurrentText(rider.name)
        self._syncing = False
        self._apply_rider(rider)

    def _on_analyze_rider_selected(self, name):
        if self._syncing or not name:
            return
        self._syncing = True
        rider = self.store.select(name)
        self.profile_tab._refresh_combo()
        self.profile_tab._load_rider(rider)
        self.plan_combo.setCurrentText(name)
        self._syncing = False
        self._apply_rider(rider)

    def _on_plan_rider_selected(self, name):
        if self._syncing or not name:
            return
        self._syncing = True
        rider = self.store.select(name)
        self.profile_tab._refresh_combo()
        self.profile_tab._load_rider(rider)
        self.analyze_combo.setCurrentText(name)
        self._syncing = False
        self._apply_rider(rider)

    # ---- measured CdA handoff ----------------------------------------
    def _save_measured_cda(self):
        results = getattr(self.analyze_gui, "analysis_results", None)
        summary = (results or {}).get("summary", {}) if isinstance(results, dict) else {}
        cda = summary.get("weighted_cda") or summary.get("average_cda")
        if not cda:
            QMessageBox.information(
                self, "No measurement",
                "Run an analysis first; then the weighted CdA can be saved to the profile.",
            )
            return
        self.profile_tab.set_cda_from_measurement(cda)
        QMessageBox.information(
            self, "Saved",
            f"Saved measured CdA {float(cda):.4f} to rider '{self.store.selected}'.",
        )

    def _show_about(self):
        try:
            self.analyze_gui._show_about_dialog()
        except Exception:
            QMessageBox.information(self, "About TriTTer",
                                    "TriTTer \u2014 unified cycling analysis & pacing.")
