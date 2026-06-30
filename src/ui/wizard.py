"""TriTTer wizard framework.

WizardFrame is a drop-in, API-compatible replacement for QTabWidget that renders
its pages as a step-by-step wizard:

    +-----------+--------------------------------+
    | 1 File    |                                |
    | 2 Rider   |        (current step)          |
    | 3 Params  |                                |
    | 4 Results |                                |
    |           |  [ Back ]   Step 3/4   [Next] |
    +-----------+--------------------------------+

It implements the subset of the QTabWidget API the app uses (addTab,
setCurrentWidget, setCurrentIndex, currentIndex, currentWidget, count, widget,
indexOf, tabText, setTabText, removeTab, currentChanged) so existing code that
builds tabs works unchanged, while the user gets a guided walkthrough with a
clickable rail (jump to any step) plus Back/Next.
"""

from PyQt5.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QStackedWidget, QListWidget,
    QListWidgetItem, QPushButton, QLabel,
)
from PyQt5.QtCore import Qt, pyqtSignal

from theme import MUTED, TEXT


class WizardFrame(QWidget):
    currentChanged = pyqtSignal(int)

    def __init__(self, rail_width=190, parent=None):
        super().__init__(parent)
        self._titles = []

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- Step rail -------------------------------------------------
        self.rail = QListWidget()
        self.rail.setObjectName("stepRail")
        self.rail.setFixedWidth(rail_width)
        self.rail.currentRowChanged.connect(self._on_rail_changed)
        root.addWidget(self.rail)

        # --- Content + nav --------------------------------------------
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        self.stack = QStackedWidget()
        right_layout.addWidget(self.stack, 1)

        nav = QHBoxLayout()
        nav.setContentsMargins(12, 8, 12, 8)
        self.back_btn = QPushButton("\u2190  Back")
        self.back_btn.setObjectName("secondary")
        self.back_btn.clicked.connect(self.back)
        nav.addWidget(self.back_btn)
        nav.addStretch()
        self.step_label = QLabel("")
        self.step_label.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        nav.addWidget(self.step_label)
        nav.addStretch()
        self.next_btn = QPushButton("Next  \u2192")
        self.next_btn.clicked.connect(self.next)
        nav.addWidget(self.next_btn)
        right_layout.addLayout(nav)

        root.addWidget(right, 1)

    # ---- QTabWidget-compatible API -----------------------------------
    def addTab(self, widget, title):
        self.stack.addWidget(widget)
        self._titles.append(title)
        idx = len(self._titles)
        item = QListWidgetItem(f"{idx}.  {title}")
        self.rail.addItem(item)
        if self.stack.count() == 1:
            self.rail.setCurrentRow(0)
            self._update_nav(0)
        return self.stack.count() - 1

    def count(self):
        return self.stack.count()

    def widget(self, index):
        return self.stack.widget(index)

    def indexOf(self, widget):
        return self.stack.indexOf(widget)

    def currentIndex(self):
        return self.stack.currentIndex()

    def currentWidget(self):
        return self.stack.currentWidget()

    def setCurrentIndex(self, index):
        if 0 <= index < self.stack.count() and index != self.stack.currentIndex():
            self.rail.setCurrentRow(index)  # drives _on_rail_changed

    def setCurrentWidget(self, widget):
        idx = self.stack.indexOf(widget)
        if idx >= 0:
            self.setCurrentIndex(idx)

    def tabText(self, index):
        if 0 <= index < len(self._titles):
            return self._titles[index]
        return ""

    def setTabText(self, index, text):
        if 0 <= index < len(self._titles):
            self._titles[index] = text
            self.rail.item(index).setText(f"{index + 1}.  {text}")

    def removeTab(self, index):
        if 0 <= index < self.stack.count():
            w = self.stack.widget(index)
            self.stack.removeWidget(w)
            self.rail.takeItem(index)
            del self._titles[index]

    # ---- navigation ---------------------------------------------------
    def back(self):
        self.setCurrentIndex(self.currentIndex() - 1)

    def next(self):
        self.setCurrentIndex(self.currentIndex() + 1)

    def _on_rail_changed(self, row):
        if row < 0:
            return
        if self.stack.currentIndex() != row:
            self.stack.setCurrentIndex(row)
        self._update_nav(row)
        self.currentChanged.emit(row)

    def _update_nav(self, row):
        total = self.stack.count()
        self.back_btn.setEnabled(row > 0)
        self.next_btn.setEnabled(row < total - 1)
        self.next_btn.setVisible(row < total - 1)
        self.step_label.setText(f"Step {row + 1} of {total}")
