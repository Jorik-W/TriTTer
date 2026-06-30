"""Open File tab — shared file picker for both Analyse and Plan modes.

One file is loaded at a time. After loading, capabilities are checked:
  - FIT with power+speed → both Analyse and Plan enabled.
  - GPX or FIT without power → Plan only; Analyse tab shows a clear message.
  - Invalid / failed → both disabled with error.

Signals
-------
fileLoaded(CourseFile)   emitted after a successful load (shell uses this to
                          push the file into Analyse/Plan and update WeatherTab
                          with the file's start time).
fileCleared()            emitted when the user clears the loaded file.
"""

import os
from pathlib import Path

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFileDialog, QPlainTextEdit, QGroupBox, QSizePolicy,
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont

from fit_loader import load_course_file, capability_check, CourseFile
from widgets import SectionHeader
from theme import MUTED, TEXT, GREEN, ORANGE, RED_COL, BORDER, SURFACE, ACCENT


class OpenFileTab(QWidget):
    """Shared file picker that gates Analyse and Plan access."""

    fileLoaded  = pyqtSignal(object)   # CourseFile
    fileCleared = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._course: CourseFile | None = None
        self._last_dir = os.path.expanduser("~")
        self._build_ui()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    @property
    def course(self) -> CourseFile | None:
        return self._course

    def load_path(self, path: str):
        """Programmatically load a file (also used by drag-drop or shell)."""
        self._do_load(path)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        layout.addWidget(SectionHeader("Course file",
            subtitle="Load a FIT or GPX file. "
                     "FIT files with power and speed enable Analyse mode. "
                     "GPX files and FIT files without power data enable Plan mode only."))

        # ── Picker row ────────────────────────────────────────────────
        pick_row = QHBoxLayout()
        self.browse_btn = QPushButton("  Browse…")
        self.browse_btn.setFixedWidth(130)
        self.browse_btn.clicked.connect(self._on_browse)

        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setObjectName("secondary")
        self.clear_btn.setFixedWidth(80)
        self.clear_btn.setEnabled(False)
        self.clear_btn.clicked.connect(self._on_clear)

        self.file_label = QLabel("No file loaded")
        self.file_label.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        self.file_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        pick_row.addWidget(self.browse_btn)
        pick_row.addWidget(self.clear_btn)
        pick_row.addWidget(self.file_label, 1)
        layout.addLayout(pick_row)

        # ── Status / capabilities banner ─────────────────────────────
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        layout.addWidget(self.status_label)

        # ── File info cards ───────────────────────────────────────────
        self.info_box = QGroupBox("File info")
        info_layout = QHBoxLayout(self.info_box)
        self._info_labels = {}
        for key, label in [
            ("type",      "Type"),
            ("distance",  "Distance"),
            ("elevation", "Elevation gain"),
            ("start",     "Start time"),
            ("channels",  "Channels"),
        ]:
            col = QVBoxLayout()
            lbl = QLabel(label)
            lbl.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
            val = QLabel("—")
            val.setStyleSheet(f"color: {TEXT}; font-size: 12px; font-weight: bold;")
            col.addWidget(lbl)
            col.addWidget(val)
            info_layout.addLayout(col)
            self._info_labels[key] = val
        self.info_box.setVisible(False)
        layout.addWidget(self.info_box)

        # ── Log ───────────────────────────────────────────────────────
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Load a file to see details…")
        self.log.setMaximumHeight(160)
        self.log.setStyleSheet(
            f"background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER}; "
            "font-family: Consolas, monospace; font-size: 11px;"
        )
        layout.addWidget(self.log)

        layout.addStretch()

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open course file", self._last_dir,
            "Course files (*.fit *.gpx);;FIT files (*.fit);;GPX files (*.gpx);;All files (*)"
        )
        if path:
            self._last_dir = os.path.dirname(path)
            self._do_load(path)

    def _on_clear(self):
        self._course = None
        self.file_label.setText("No file loaded")
        self.file_label.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        self.clear_btn.setEnabled(False)
        self.status_label.setText("")
        self.info_box.setVisible(False)
        self.log.clear()
        self.fileCleared.emit()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _do_load(self, path: str):
        self.browse_btn.setEnabled(False)
        self.log.clear()
        self._append_log(f"Loading: {path}")

        try:
            course = load_course_file(path, status_callback=self._append_log)
        except Exception as e:
            self._set_error(f"Unexpected error: {e}")
            self.browse_btn.setEnabled(True)
            return

        self.browse_btn.setEnabled(True)

        if not course.valid:
            self._set_error(course.error or "Failed to load file")
            return

        self._course = course
        caps = capability_check(course)

        # ── Update UI ────────────────────────────────────────────────
        self.file_label.setText(Path(path).name)
        self.file_label.setStyleSheet(f"color: {TEXT}; font-size: 12px; font-weight: bold;")
        self.clear_btn.setEnabled(True)

        if caps.analyse_ok:
            self.status_label.setText("✓ Analyse and Plan modes available")
            self.status_label.setStyleSheet(f"color: {GREEN}; font-size: 11px;")
        else:
            self.status_label.setText(f"⚠  {caps.message}")
            self.status_label.setStyleSheet(f"color: {ORANGE}; font-size: 11px;")

        # Populate info cards
        chans = []
        if course.has_power: chans.append("power")
        if course.has_speed: chans.append("speed")
        if course.has_gps:   chans.append("GPS")
        if any(v is not None for v in course.heart_rate): chans.append("HR")
        if any(v is not None for v in course.cadence):    chans.append("cadence")

        self._info_labels["type"].setText(course.file_type.upper())
        self._info_labels["distance"].setText(f"{course.total_distance/1000:.1f} km")
        self._info_labels["elevation"].setText(f"{course.total_elevation:.0f} m")
        self._info_labels["start"].setText(
            str(course.start_time)[:16] if course.start_time else "—"
        )
        self._info_labels["channels"].setText(", ".join(chans) or "geometry only")
        self.info_box.setVisible(True)

        self._append_log(
            f"Loaded {len(course.distances)} points  |  "
            f"{course.total_distance/1000:.1f} km  |  "
            f"+{course.total_elevation:.0f} m  |  "
            f"power: {'yes' if course.has_power else 'no'}  |  "
            f"speed: {'yes' if course.has_speed else 'no'}"
        )

        self.fileLoaded.emit(course)

    def _set_error(self, msg: str):
        self._course = None
        self.file_label.setText("Load failed")
        self.file_label.setStyleSheet(f"color: {RED_COL}; font-size: 12px;")
        self.status_label.setText(f"Error: {msg}")
        self.status_label.setStyleSheet(f"color: {RED_COL}; font-size: 11px;")
        self.info_box.setVisible(False)
        self._append_log(f"Error: {msg}")

    def _append_log(self, msg: str):
        self.log.appendPlainText(msg)
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())
