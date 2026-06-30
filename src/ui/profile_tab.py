"""Profile tab: edit and manage rider profiles (single source of truth)."""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QDoubleSpinBox, QPushButton, QComboBox, QGroupBox, QPlainTextEdit,
    QMessageBox,
)
from PyQt5.QtCore import pyqtSignal, Qt
from PyQt5.QtGui import QFont

from profiles import Rider


class ProfileTab(QWidget):
    """Create/edit/delete riders. Emits ``riderChanged`` when the active
    rider's data changes so the Analyze/Plan tabs can refresh."""

    riderChanged = pyqtSignal(object)  # emits the active Rider

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self._loading = False
        self._build_ui()
        self._refresh_combo()
        self._load_rider(self.store.get_selected())

    # ---- UI -----------------------------------------------------------
    def _build_ui(self):
        layout = QVBoxLayout(self)

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
        form = QFormLayout(box)

        self.f_name = QLineEdit()
        form.addRow("Name", self.f_name)

        self.f_rider_mass = self._spin(30, 150, 0.1, " kg")
        form.addRow("Rider mass", self.f_rider_mass)
        self.f_bike_mass = self._spin(3, 30, 0.1, " kg")
        form.addRow("Bike mass", self.f_bike_mass)
        self.f_crr = self._spin(0.0, 0.02, 0.0001, "", 4)
        form.addRow("Rolling resistance (Crr)", self.f_crr)
        self.f_dtloss = self._spin(0.0, 10.0, 0.1, " %", 1)
        form.addRow("Drivetrain loss", self.f_dtloss)
        self.f_cda = self._spin(0.10, 0.60, 0.001, "", 3)
        form.addRow("CdA (measured / manual)", self.f_cda)
        self.f_climb_cda = self._spin(0.10, 0.70, 0.001, "", 3)
        form.addRow("Climbing CdA", self.f_climb_cda)
        self.f_wind = self._spin(0.0, 1.0, 0.01, "", 2)
        form.addRow("Wind effect factor", self.f_wind)
        self.f_ftp = self._spin(80, 600, 1, " W", 0)
        form.addRow("FTP", self.f_ftp)

        self.f_notes = QPlainTextEdit()
        self.f_notes.setFixedHeight(60)
        form.addRow("Notes", self.f_notes)

        layout.addWidget(box)

        # Buttons
        btn_row = QHBoxLayout()
        save_btn = QPushButton("Save changes")
        save_btn.clicked.connect(self._on_save)
        btn_row.addStretch()
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)
        layout.addStretch()

    def _spin(self, lo, hi, step, suffix="", decimals=2):
        sp = QDoubleSpinBox()
        sp.setRange(lo, hi)
        sp.setSingleStep(step)
        sp.setDecimals(decimals)
        if suffix:
            sp.setSuffix(suffix)
        return sp

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
        self.f_rider_mass.setValue(rider.rider_mass)
        self.f_bike_mass.setValue(rider.bike_mass)
        self.f_crr.setValue(rider.rolling_resistance)
        self.f_dtloss.setValue(rider.drivetrain_loss * 100.0)
        self.f_cda.setValue(rider.cda)
        self.f_climb_cda.setValue(rider.climbing_cda)
        self.f_wind.setValue(rider.wind_effect_factor)
        self.f_ftp.setValue(rider.ftp)
        self.f_notes.setPlainText(rider.notes)
        self._loading = False

    def _form_to_rider(self):
        return Rider(
            name=self.f_name.text().strip() or "New Rider",
            rider_mass=self.f_rider_mass.value(),
            bike_mass=self.f_bike_mass.value(),
            rolling_resistance=self.f_crr.value(),
            drivetrain_loss=self.f_dtloss.value() / 100.0,
            cda=self.f_cda.value(),
            climbing_cda=self.f_climb_cda.value(),
            wind_effect_factor=self.f_wind.value(),
            ftp=self.f_ftp.value(),
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
        self._load_rider(rider)
        self.riderChanged.emit(rider)
