"""TriTTer main window: five top-level tabs.

Tab order: (Open File) | Weather | Profile | Analyse | Plan
Open File tab is added in Phase 3; Weather is added here in Phase 2.
"""

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QTabWidget, QMessageBox,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QObject
from PyQt5.QtGui import QFont, QIcon

from profiles import ProfileStore
from profile_tab import ProfileTab
from weather_tab import WeatherTab
from open_file_tab import OpenFileTab
from qt_gui import GUIInterface, resource_path
from plan_gui import PlanTab


class _WeatherFetchWorker(QObject):
    """Background thread worker: fetches multi-point weather along a course."""
    finished = pyqtSignal(dict)   # result dict on success
    failed   = pyqtSignal(str)    # error string on failure

    def __init__(self, distances, latitudes, longitudes, timestamps, start_time, mode):
        super().__init__()
        self._distances  = distances
        self._latitudes  = latitudes
        self._longitudes = longitudes
        self._timestamps = timestamps
        self._start_time = start_time
        self._mode       = mode

    def run(self):
        try:
            import sys, os
            _plan_dir = os.path.normpath(
                os.path.join(os.path.dirname(__file__), '..', 'plan'))
            if _plan_dir not in sys.path:
                sys.path.insert(0, _plan_dir)
            from weather_plan import fetch_weather_samples
            samples = fetch_weather_samples(
                self._distances, self._latitudes, self._longitudes,
                mode=self._mode,
                start_time=self._start_time,
                timestamps=self._timestamps,
            )
            if not samples:
                self.finished.emit({"samples": [], "available": False,
                                    "weather_samples": []})
                return
            display = [{
                "temperature_2m":       s["weather"].get("temperature"),
                "wind_speed_10m":       s["weather"].get("wind_speed"),
                "wind_direction_10m":   s["weather"].get("wind_direction"),
                "surface_pressure":     s["weather"].get("pressure"),
                "relative_humidity_2m": s["weather"].get("humidity"),
            } for s in samples if s.get("weather")]
            self.finished.emit({
                "samples":         display,
                "available":       True,
                "weather_samples": samples,
                "sample_count":    len(samples),
            })
        except Exception as exc:
            self.failed.emit(str(exc))


class TriTTerWindow(QMainWindow):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setWindowTitle("TriTTer")
        self.resize(1280, 1040)
        try:
            self.setWindowIcon(QIcon(resource_path("icons/logo.PNG")))
            app.setWindowIcon(QIcon(resource_path("icons/logo.PNG")))
        except Exception:
            pass

        self.store = ProfileStore()
        self._syncing = False
        self._tab_fetch_thread = None   # keep alive until fetch completes
        self._tab_fetch_worker = None

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
        self.open_file_tab.manualCourseChanged.connect(self._on_manual_course_changed)
        self.tabs.addTab(self.open_file_tab, "Open File")

        # --- Weather tab (Phase 2) ---
        self.weather_tab = WeatherTab()
        self.weather_tab.weatherChanged.connect(self._on_weather_changed)
        self.tabs.addTab(self.weather_tab, "Weather")

        # --- Profile tab ---
        self.profile_tab = ProfileTab(self.store)
        self.profile_tab.riderChanged.connect(self._on_profile_rider_changed)
        self.profile_tab.formDirty.connect(lambda: self._set_profile_dirty(True))
        self.tabs.addTab(self.profile_tab, "Profile")

        # --- Analyse tab ---
        self.analyze_gui = GUIInterface(app)
        self.analyze_gui.windEffectChanged.connect(self._on_analyze_wef_changed)
        self._analyse_page = self._build_analyze_page()
        self.tabs.addTab(self._analyse_page, "Analyse")

        # --- Plan tab ---
        self.plan_gui = PlanTab()
        self.plan_gui.windEffectChanged.connect(self._on_plan_wef_changed)
        self._plan_page = self._build_plan_page()
        self.tabs.addTab(self._plan_page, "Plan")

        # Apply the initially selected rider.
        self._apply_rider(self.store.get_selected())

        # Track tab switches for compute gating and profile dirty checks.
        self._prev_tab_index = self.tabs.currentIndex()
        self._plan_stale = False
        self._analyse_stale = False
        self._profile_dirty = False
        self.tabs.currentChanged.connect(self._on_tab_changed)

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
    @staticmethod
    def _utc_to_local(st):
        """Convert a UTC datetime (naive or aware) to a naive local datetime."""
        import datetime as _dt
        if st is None:
            return None
        if st.tzinfo is None:
            st = st.replace(tzinfo=_dt.timezone.utc)
        return st.astimezone(tz=None).replace(tzinfo=None)

    def _on_file_loaded(self, course):
        """A file was picked in the Open File tab — push to Analyse and Plan."""
        path = course.path

        # Update Weather tab with the file's start time (convert UTC → local).
        if course.start_time is not None:
            from PyQt5.QtCore import QDateTime
            try:
                st_local = self._utc_to_local(course.start_time)
                if st_local is not None:
                    qdt = QDateTime(st_local.year, st_local.month, st_local.day,
                                    st_local.hour, st_local.minute, st_local.second)
                    self.weather_tab.set_file_start_time(qdt, has_power=course.has_power)
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
        self.weather_tab.clear_file_time()
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
            course = self.open_file_tab.course
            if course is None or not course.has_gps:
                self.weather_tab.show_fetch_result(
                    {}, error="Load a FIT/GPX file with GPS data first.")
                return
            if not course.latitudes or not any(v is not None for v in course.latitudes):
                self.weather_tab.show_fetch_result(
                    {}, error="No GPS coordinates found in loaded file.")
                return

            import datetime as _dt
            from weather_plan import MODE_FORECAST, MODE_HISTORY

            time_mode = cfg.get("api_time_mode", "file")
            timestamps = None
            start_time = None
            mode = MODE_FORECAST

            if time_mode == "file" and course.timestamps:
                # Convert UTC → local; forward-fill None entries.
                raw = [self._utc_to_local(t) if t is not None else None
                       for t in course.timestamps]
                filled = []
                last = None
                for t in raw:
                    if t is not None:
                        last = t
                    filled.append(last)
                # Back-fill any remaining leading Nones.
                first_real = next((t for t in filled if t is not None), None)
                if first_real is not None:
                    filled = [t if t is not None else first_real for t in filled]
                if any(t is not None for t in filled):
                    timestamps = filled
                    mode = MODE_HISTORY
                else:
                    start_time = self._utc_to_local(course.start_time) or _dt.datetime.now()
            else:
                qdt = cfg.get("api_datetime")
                if qdt is not None:
                    start_time = _dt.datetime(
                        qdt.date().year(), qdt.date().month(), qdt.date().day(),
                        qdt.time().hour(), qdt.time().minute())
                else:
                    start_time = self._utc_to_local(course.start_time) or _dt.datetime.now()

            self._run_weather_fetch(
                course.distances, course.latitudes, course.longitudes,
                timestamps, start_time, mode)
            return

        if cfg.get("applies_analyse", True):
            try:
                self.analyze_gui.apply_weather_from_tab(cfg)
            except Exception:
                pass
            if self.tabs.currentIndex() != self.tabs.indexOf(self._analyse_page):
                self._analyse_stale = True
        if cfg.get("applies_plan", True):
            plan_visible = (self.tabs.currentIndex() == self.tabs.indexOf(self._plan_page))
            try:
                self.plan_gui.apply_weather_from_tab(cfg, recalc=plan_visible)
            except Exception:
                pass
            if not plan_visible:
                self._plan_stale = True

    def _run_weather_fetch(self, distances, latitudes, longitudes, timestamps, start_time, mode):
        """Start a background multi-point weather fetch and wire result back to weather_tab."""
        thread = QThread(self)
        worker = _WeatherFetchWorker(distances, latitudes, longitudes,
                                     timestamps, start_time, mode)
        worker.moveToThread(thread)
        # Keep strong Python references so GC doesn't collect before the signal fires.
        self._tab_fetch_thread = thread
        self._tab_fetch_worker = worker

        def on_done(result):
            self.weather_tab.show_fetch_result(result)
            thread.quit()
            self._tab_fetch_thread = None
            self._tab_fetch_worker = None

        def on_fail(err):
            self.weather_tab.show_fetch_result({}, error=err)
            thread.quit()
            self._tab_fetch_thread = None
            self._tab_fetch_worker = None

        thread.started.connect(worker.run)
        worker.finished.connect(on_done)
        worker.failed.connect(on_fail)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    # ---- rider sync ---------------------------------------------------
    def _on_analyze_wef_changed(self, value: float):
        """Analyse WEF slider released — push to weather tab (which propagates back silently)."""
        try:
            self.weather_tab.s_wef.set_value(value, silent=False)
        except Exception:
            pass

    def _on_plan_wef_changed(self, value: float):
        """Plan WEF slider released — push to weather tab + analyse."""
        try:
            self.weather_tab.s_wef.set_value(value, silent=False)
        except Exception:
            pass

    def _apply_rider(self, rider):
        if rider is None:
            return
        analyse_idx = self.tabs.indexOf(self._analyse_page)
        plan_idx = self.tabs.indexOf(self._plan_page)
        current = self.tabs.currentIndex()

        if current == analyse_idx:
            self.analyze_gui.apply_rider(rider)
        else:
            self._analyse_stale = True
            # Still push values silently so they're ready when tab is shown.
            try:
                self.analyze_gui.apply_rider(rider)
            except Exception:
                pass

        if current == plan_idx:
            try:
                self.plan_gui.apply_rider(rider)
            except Exception:
                pass
        else:
            self._plan_stale = True
            # Push values silently but skip recalculation.
            try:
                self.plan_gui._apply_rider_silent(rider)
            except Exception:
                pass

    def _on_manual_course_changed(self, dist_km, elev_m, climb_grad, desc_grad):
        try:
            self.plan_gui.set_manual_course(dist_km, elev_m, climb_grad, desc_grad)
        except Exception:
            pass

    def _refresh_rider_combos(self, selected: str | None = None):
        """Rebuild analyse & plan combobox items from store."""
        self._syncing = True
        for combo in (self.analyze_combo, self.plan_combo):
            combo.clear()
            combo.addItems(self.store.names())
            if selected:
                combo.setCurrentText(selected)
        self._syncing = False

    def _on_profile_rider_changed(self, rider):
        if self._syncing or rider is None:
            return
        self._syncing = True
        self._refresh_rider_combos(rider.name)
        self._syncing = False
        self._set_profile_dirty(False)
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

    # ---- tab switch gating -------------------------------------------
    def _on_tab_changed(self, index):
        """Gate computation to visible tab; nuke results on Weather switch."""
        prev = self._prev_tab_index
        self._prev_tab_index = index

        weather_idx = self.tabs.indexOf(self.weather_tab)
        profile_idx = self.tabs.indexOf(self.profile_tab)
        analyse_idx = self.tabs.indexOf(self._analyse_page)
        plan_idx = self.tabs.indexOf(self._plan_page)

        # Leaving Profile tab with unsaved changes → prompt.
        if prev == profile_idx and self._profile_dirty:
            reply = QMessageBox.question(
                self, "Unsaved profile",
                "You have unsaved profile changes.\n\nSave before switching?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save,
            )
            if reply == QMessageBox.Cancel:
                self.tabs.blockSignals(True)
                self.tabs.setCurrentIndex(prev)
                self.tabs.blockSignals(False)
                self._prev_tab_index = prev
                return
            elif reply == QMessageBox.Save:
                self.profile_tab._on_save()
            else:
                # Discard: reload stored rider into form.
                rider = self.store.get_selected()
                if rider:
                    self.profile_tab._load_rider(rider)
            self._set_profile_dirty(False)

        # Switching TO Weather tab → mark Plan + Analyse as stale (nuke results).
        if index == weather_idx:
            self._plan_stale = True
            self._analyse_stale = True
            try:
                self.plan_gui.advanced_results.show_no_file()
                self.plan_gui._set_advanced_full_current(False)
            except Exception:
                pass
            try:
                self.analyze_gui._cleanup_results(full_reset=False)
            except Exception:
                pass

        # Switching TO Plan → recalculate if stale.
        elif index == plan_idx and self._plan_stale:
            self._plan_stale = False
            try:
                self.plan_gui._recalculate()
            except Exception:
                pass

        # Switching TO Analyse → recalculate if stale.
        elif index == analyse_idx and self._analyse_stale:
            self._analyse_stale = False
            try:
                self.analyze_gui._run_analysis()
            except Exception:
                pass

    def _set_profile_dirty(self, dirty):
        """Mark/unmark the Profile tab as having unsaved changes."""
        self._profile_dirty = dirty
        profile_idx = self.tabs.indexOf(self.profile_tab)
        title = "Profile*" if dirty else "Profile"
        self.tabs.setTabText(profile_idx, title)

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
