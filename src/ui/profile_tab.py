"""Profile tab: edit and manage rider profiles (single source of truth)."""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QComboBox, QGroupBox, QPlainTextEdit,
    QMessageBox, QScrollArea,
)
from PyQt5.QtCore import pyqtSignal, Qt
from PyQt5.QtGui import QFont

from profiles import Rider
from widgets import SliderRow
from theme import MUTED


class ProfileTab(QWidget):
    """Create/edit/delete riders. Emits ``riderChanged`` when the active
    rider's data changes so the Analyze/Plan tabs can refresh."""

    riderChanged = pyqtSignal(object)  # emits the active Rider
    formDirty = pyqtSignal()  # emits when any field is modified without saving

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self._loading = False
        self._build_ui()
        self._refresh_combo()
        self._load_rider(self.store.get_selected())

    # ---- UI -----------------------------------------------------------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        root.addWidget(scroll)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)
        scroll.setWidget(inner)

        title = QLabel("Rider Profiles")
        title.setFont(QFont("Arial", 14, QFont.Bold))
        layout.addWidget(title, alignment=Qt.AlignCenter)

        info = QLabel("Profiles are the single source of truth for rider parameters. "
                      "A rider selected here is used by the Analyze and Plan tabs. "
                      "Saved to ~/.tritter/profiles.json.")
        info.setWordWrap(True)
        layout.addWidget(info)

        # Selector row
        sel_row = QHBoxLayout()
        sel_row.addWidget(QLabel("Active rider:"))
        self.combo = QComboBox()
        self.combo.currentTextChanged.connect(self._on_select)
        sel_row.addWidget(self.combo, 1)
        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._on_add)
        del_btn = QPushButton("Delete")
        del_btn.clicked.connect(self._on_delete)
        sel_row.addWidget(add_btn)
        sel_row.addWidget(del_btn)
        layout.addLayout(sel_row)

        # Editor
        box = QGroupBox("Rider parameters")
        box_layout = QVBoxLayout(box)
        box_layout.setSpacing(4)

        LW = 220   # label column width shared with SliderRow

        # Name (no slider — free text)
        name_row = QHBoxLayout()
        name_lbl = QLabel("Name")
        name_lbl.setFixedWidth(LW)
        name_lbl.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        self.f_name = QLineEdit()
        self.f_name.textChanged.connect(self._mark_dirty)
        name_row.addWidget(name_lbl)
        name_row.addWidget(self.f_name)
        box_layout.addLayout(name_row)

        # Numeric fields as SliderRow
        self.f_rider_mass = SliderRow("Rider mass",              30,   150,  75.0,  0.1,    1, " kg", label_width=LW)
        self.f_bike_mass  = SliderRow("Bike mass",                3,    30,  10.0,  0.1,    1, " kg", label_width=LW)
        self.f_crr        = SliderRow("Rolling resistance (Crr)", 0.0,  0.02, 0.005, 0.0001, 4, "",   label_width=LW)
        self.f_dtloss     = SliderRow("Drivetrain loss",          0.0,  10.0, 2.5,   0.1,    1, " %", label_width=LW)
        self.f_cda        = SliderRow("CdA (measured / manual)",  0.10, 0.60, 0.290, 0.001,  3, "",   label_width=LW)
        self.f_climb_cda  = SliderRow("Climbing CdA",             0.10, 0.70, 0.310, 0.001,  3, "",   label_width=LW)
        self.f_ftp        = SliderRow("FTP",                      80,   600,  309,   1,      0, " W", label_width=LW)
        self.f_max_power  = SliderRow("Max power",               100,  2000,  400,   1,      0, " W", label_width=LW)
        self.f_reserve    = SliderRow("W\u2019 reserve capacity",  5,    60,   20,   0.5,    1, " kJ", label_width=LW)
        self.f_min_reserve= SliderRow("W\u2019 min reserve warn",  0,    40,   15,   0.5,    1, " kJ", label_width=LW)
        self.f_decay      = SliderRow("W\u2019 reserve decay",     0.05, 0.60, 0.20, 0.01,   2, "",   label_width=LW)
        for w in [self.f_rider_mass, self.f_bike_mass, self.f_crr, self.f_dtloss,
                  self.f_cda, self.f_climb_cda, self.f_ftp, self.f_max_power,
                  self.f_reserve, self.f_min_reserve, self.f_decay]:
            box_layout.addWidget(w)
            w.interactionFinished.connect(self._mark_dirty)

        # Notes (no slider — free text)
        notes_row = QHBoxLayout()
        notes_lbl = QLabel("Notes")
        notes_lbl.setFixedWidth(LW)
        notes_lbl.setAlignment(Qt.AlignTop)
        notes_lbl.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        self.f_notes = QPlainTextEdit()
        self.f_notes.setFixedHeight(60)
        self.f_notes.textChanged.connect(self._mark_dirty)
        notes_row.addWidget(notes_lbl)
        notes_row.addWidget(self.f_notes)
        box_layout.addLayout(notes_row)

        layout.addWidget(box)

        # Buttons
        btn_row = QHBoxLayout()
        save_btn = QPushButton("Save changes")
        save_btn.clicked.connect(self._on_save)
        btn_row.addStretch()
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)
        layout.addStretch()

    # ---- data binding -------------------------------------------------
    def _refresh_combo(self):
        self._loading = True
        self.combo.clear()
        self.combo.addItems(self.store.names())
        if self.store.selected:
            self.combo.setCurrentText(self.store.selected)
        self._loading = False

    def _load_rider(self, rider):
        if rider is None:
            return
        self._loading = True
        self.f_name.setText(rider.name)
        self.f_rider_mass.set_value(rider.rider_mass)
        self.f_bike_mass.set_value(rider.bike_mass)
        self.f_crr.set_value(rider.rolling_resistance)
        self.f_dtloss.set_value(rider.drivetrain_loss * 100.0)
        self.f_cda.set_value(rider.cda)
        self.f_climb_cda.set_value(rider.climbing_cda)
        self.f_ftp.set_value(rider.ftp)
        self.f_max_power.set_value(getattr(rider, 'max_power', 400.0))
        self.f_reserve.set_value(getattr(rider, 'reserve_kj', 20.0))
        self.f_min_reserve.set_value(getattr(rider, 'min_reserve_kj', 15.0))
        self.f_decay.set_value(getattr(rider, 'reserve_decay', 0.20))
        self.f_notes.setPlainText(rider.notes)
        self._loading = False

    def _mark_dirty(self, *_args):
        if not self._loading:
            self.formDirty.emit()

    def _form_to_rider(self):
        return Rider(
            name=self.f_name.text().strip() or "New Rider",
            rider_mass=self.f_rider_mass.value(),
            bike_mass=self.f_bike_mass.value(),
            rolling_resistance=self.f_crr.value(),
            drivetrain_loss=self.f_dtloss.value() / 100.0,
            cda=self.f_cda.value(),
            climbing_cda=self.f_climb_cda.value(),
            ftp=self.f_ftp.value(),
            max_power=self.f_max_power.value(),
            reserve_kj=self.f_reserve.value(),
            min_reserve_kj=self.f_min_reserve.value(),
            reserve_decay=self.f_decay.value(),
            notes=self.f_notes.toPlainText().strip(),
        )

    # ---- actions ------------------------------------------------------
    def _on_select(self, name):
        if self._loading or not name:
            return
        rider = self.store.select(name)
        self._load_rider(rider)
        self.riderChanged.emit(rider)

    def _on_add(self):
        rider = self.store.add(Rider())
        self._refresh_combo()
        self.combo.setCurrentText(rider.name)
        self._load_rider(rider)
        self.riderChanged.emit(rider)

    def _on_delete(self):
        name = self.store.selected
        if name is None:
            return
        if QMessageBox.question(self, "Delete rider", f"Delete '{name}'?") != QMessageBox.Yes:
            return
        self.store.remove(name)
        self._refresh_combo()
        self._load_rider(self.store.get_selected())
        self.riderChanged.emit(self.store.get_selected())

    def _on_save(self):
        old_name = self.store.selected
        rider = self._form_to_rider()
        # Preserve identity if the name field was left unchanged.
        if old_name and rider.name == old_name:
            self.store.update(rider)
        else:
            # Renamed: replace the old entry.
            existing = self.store.get(old_name)
            if existing is not None and rider.name not in self.store.names():
                self.store.remove(old_name)
            rider = self.store.add(rider)
        self._refresh_combo()
        self.combo.setCurrentText(self.store.selected)
        self.riderChanged.emit(self.store.get_selected())
        QMessageBox.information(self, "Saved", f"Profile '{rider.name}' saved.")

    def set_cda_from_measurement(self, cda_value):
        """Write a measured CdA (from Analyze) into the active rider profile."""
        rider = self.store.get_selected()
        if rider is None:
            return
        rider.cda = round(float(cda_value), 4)
        self.store.update(rider)
        self.f_cda.set_value(rider.cda)
        self.riderChanged.emit(rider)
