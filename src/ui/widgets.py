"""TriTTer shared UI widgets.

Promoted from bike_estimator's plan/ui/widgets.py so both Analyze and Plan use
the exact same input controls. The key widget is SliderRow: a compact
slider + spin-box pair (bike_estimator style) laid out densely (cda_analyzer
style), which is the uniform input the project standardizes on.
"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSlider, QFrame, QDoubleSpinBox,
    QCheckBox,
)
from PyQt5.QtCore import Qt, pyqtSignal

from theme import ACCENT, CARD, TEXT, MUTED, BORDER


class SectionHeader(QWidget):
    """A numbered step/section heading with an optional subtitle."""

    def __init__(self, title, subtitle="", number=None, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 6)
        layout.setSpacing(2)
        text = f"{number}.  {title}" if number is not None else title
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color: {TEXT}; font-size: 17px; font-weight: bold;")
        layout.addWidget(lbl)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setWordWrap(True)
            sub.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
            layout.addWidget(sub)
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet(f"color: {BORDER}; background: {BORDER}; max-height: 1px;")
        layout.addWidget(line)


class MetricCard(QFrame):
    """A small card showing a labelled metric value (used on Results)."""

    def __init__(self, label, value="\u2014", unit="", accent=False):
        super().__init__()
        self.setObjectName("card")
        self.setMinimumHeight(64)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(2)
        self.lbl = QLabel(label)
        self.lbl.setStyleSheet(f"background: transparent; color: {MUTED}; font-size: 10px; border: none;")
        layout.addWidget(self.lbl)
        color = ACCENT if accent else TEXT
        self.val = QLabel(value)
        self.val.setStyleSheet(f"background: transparent; color: {color}; font-size: 19px; font-weight: bold; border: none;")
        layout.addWidget(self.val)
        if unit:
            unit_lbl = QLabel(unit)
            unit_lbl.setStyleSheet(f"background: transparent; color: {MUTED}; font-size: 10px; border: none;")
            layout.addWidget(unit_lbl)

    def update_value(self, value, color=None):
        self.val.setText(str(value))
        if color:
            self.val.setStyleSheet(
                f"background: transparent; color: {color}; font-size: 19px; "
                f"font-weight: bold; border: none;"
            )


class SliderRow(QWidget):
    """Compact slider + spin-box pair bound to a single float value.

    label | [======slider======] | [ spinbox ]
    """

    valueChanged = pyqtSignal(float)
    interactionFinished = pyqtSignal()

    def __init__(self, label, min_val, max_val, default, step,
                 decimals=0, suffix="", label_width=150, parent=None):
        super().__init__(parent)
        self.min_val = min_val
        self.max_val = max_val
        self.step = step
        self._decimals = decimals
        self._steps = max(1, round((max_val - min_val) / step))
        self._updating = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(8)

        self._label = QLabel(label)
        self._label.setFixedWidth(label_width)
        self._label.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        layout.addWidget(self._label)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setMinimum(0)
        self.slider.setMaximum(self._steps)
        self.slider.setValue(self._to_slider(default))
        self.slider.valueChanged.connect(self._on_slider_change)
        self.slider.sliderReleased.connect(self.interactionFinished.emit)
        layout.addWidget(self.slider, 1)

        self.spin = QDoubleSpinBox()
        self.spin.setRange(min_val, max_val)
        self.spin.setSingleStep(step)
        self.spin.setDecimals(decimals)
        self.spin.setValue(default)
        if suffix:
            self.spin.setSuffix(suffix)
        self.spin.setFixedWidth(108)
        self.spin.setKeyboardTracking(False)
        self.spin.setAlignment(Qt.AlignRight)
        self.spin.valueChanged.connect(self._on_spin_change)
        self.spin.editingFinished.connect(self.interactionFinished.emit)
        layout.addWidget(self.spin)

    def _to_slider(self, v):
        return max(0, min(self._steps, round((v - self.min_val) / self.step)))

    def _to_value(self, s):
        return self.min_val + s * self.step

    def _on_slider_change(self, slider_val):
        if self._updating:
            return
        self._updating = True
        v = self._to_value(slider_val)
        self.spin.blockSignals(True)
        self.spin.setValue(v)
        self.spin.blockSignals(False)
        self._updating = False
        self.valueChanged.emit(v)

    def _on_spin_change(self, v):
        if self._updating:
            return
        self._updating = True
        v = max(self.min_val, min(self.max_val, v))
        v = self.min_val + round((v - self.min_val) / self.step) * self.step
        v = round(v, self._decimals)
        self.spin.blockSignals(True)
        self.spin.setValue(v)
        self.spin.blockSignals(False)
        self.slider.blockSignals(True)
        self.slider.setValue(self._to_slider(v))
        self.slider.blockSignals(False)
        self._updating = False
        self.valueChanged.emit(v)

    def value(self):
        return self.spin.value()

    def set_value(self, v, silent=False):
        was = self._updating
        self._updating = True
        v = max(self.min_val, min(self.max_val, v))
        self.spin.blockSignals(True)
        self.spin.setValue(v)
        self.spin.blockSignals(False)
        self.slider.blockSignals(True)
        self.slider.setValue(self._to_slider(v))
        self.slider.blockSignals(False)
        self._updating = was
        if not silent:
            self.valueChanged.emit(v)

    def setEnabled(self, enabled):
        super().setEnabled(enabled)
        self.slider.setEnabled(enabled)
        self.spin.setEnabled(enabled)


class CheckRow(QWidget):
    """A compact labelled checkbox row matching SliderRow's metrics."""

    toggled = pyqtSignal(bool)

    def __init__(self, label, checked=False, label_width=150, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(8)
        self._label = QLabel(label)
        self._label.setFixedWidth(label_width)
        self._label.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        layout.addWidget(self._label)
        self.check = QCheckBox()
        self.check.setChecked(checked)
        self.check.toggled.connect(self.toggled.emit)
        layout.addWidget(self.check)
        layout.addStretch()

    def isChecked(self):
        return self.check.isChecked()

    def setChecked(self, v):
        self.check.setChecked(v)
