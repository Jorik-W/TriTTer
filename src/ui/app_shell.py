"""TriTTer main window: three top-level tabs (Profile / Analyze / Plan).

The shell hosts:
  * Profile  - rider profiles (single source of truth).
  * Analyze  - the CdA analyzer (recorded ride -> CdA), embedded as-is.
  * Plan     - pacing/time estimator (ported in the next phase; placeholder now).

Rider selection is shared: choosing a rider in either Profile or Analyze keeps
the others in sync, and a measured CdA can be written back to the active profile.
"""

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QTabWidget, QMessageBox,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont

from profiles import ProfileStore
from profile_tab import ProfileTab
from qt_gui import GUIInterface


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

        # --- Profile tab ---
        self.profile_tab = ProfileTab(self.store)
        self.profile_tab.riderChanged.connect(self._on_profile_rider_changed)
        self.tabs.addTab(self.profile_tab, "Profile")

        # --- Analyze tab (embed existing GUIInterface) ---
        self.analyze_gui = GUIInterface(app)
        self.tabs.addTab(self._build_analyze_page(), "Analyze")

        # --- Plan tab (placeholder for next phase) ---
        self.tabs.addTab(self._build_plan_placeholder(), "Plan")

        # Apply the initially selected rider to Analyze.
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

    def _build_plan_placeholder(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("Plan (Pacing)")
        title.setFont(QFont("Arial", 14, QFont.Bold))
        layout.addWidget(title, alignment=Qt.AlignCenter)
        msg = QLabel(
            "The Plan (pacing/time estimator) is being ported from bike_estimator "
            "in the next phase.\n\nIt will use the selected rider's CdA, mass, Crr and "
            "FTP from the Profile tab, share the unified weather/physics core, and add "
            "an elevation graph + folium map plus FIT export / ADB push."
        )
        msg.setWordWrap(True)
        msg.setAlignment(Qt.AlignCenter)
        layout.addWidget(msg)
        layout.addStretch()
        return page

    # ---- rider sync ---------------------------------------------------
    def _apply_rider(self, rider):
        if rider is None:
            return
        self.analyze_gui.apply_rider(rider)

    def _on_profile_rider_changed(self, rider):
        if self._syncing or rider is None:
            return
        self._syncing = True
        self.analyze_combo.setCurrentText(rider.name)
        self._syncing = False
        self._apply_rider(rider)

    def _on_analyze_rider_selected(self, name):
        if self._syncing or not name:
            return
        self._syncing = True
        rider = self.store.select(name)
        self.profile_tab._refresh_combo()
        self.profile_tab._load_rider(rider)
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
            f"Saved measured CdA {float(cda):.4f} m\u00b2 to rider '{self.store.selected}'.",
        )
