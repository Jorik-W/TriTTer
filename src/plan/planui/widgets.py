"""
Plan-specific UI widgets: ProfileBar, ElevationPlot.

MetricCard and SliderRow are imported from the shared ui/widgets module so
every part of the app uses the exact same slider and card widget.
"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
)
from PyQt5.QtCore import Qt, pyqtSignal

from theme import ACCENT, CARD, TEXT, MUTED, ORANGE, GREEN, RED_COL

# Re-export all shared widgets so plan modules have a single import point.
from widgets import MetricCard, SliderRow, SectionHeader, CheckRow  # noqa: F401  (shared ui/widgets)


def fmt_time(h, with_seconds=False):  # noqa: F401 — re-exported for plan callers
    """Format a fractional-hour value as 'Xh MMmin [SSs]'."""
    if with_seconds:
        total_s = round(h * 3600)
        hh, rem = divmod(total_s, 3600)
        mm, ss = divmod(rem, 60)
        return f"{hh}h {mm:02d}min {ss:02d}s"
    hh = int(h)
    mm = round((h - hh) * 60)
    if mm == 60:
        hh += 1
        mm = 0
    return f"{hh}h {mm:02d}min"

try:
    import pyqtgraph as pg
    HAS_PG = True
except ImportError:
    HAS_PG = False


class ProfileBar(QWidget):
    """Stacked horizontal bar showing climb/flat/descent proportions."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(48)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self.bar_layout = QHBoxLayout()
        self.bar_layout.setSpacing(2)
        self.bar_layout.setContentsMargins(0, 0, 0, 0)
        self.climb_bar = QFrame()
        self.climb_bar.setStyleSheet(f"background: {ORANGE}; border-radius: 3px;")
        self.climb_bar.setFixedHeight(14)
        self.flat_bar = QFrame()
        self.flat_bar.setStyleSheet(f"background: #5A5A72; border-radius: 3px;")
        self.flat_bar.setFixedHeight(14)
        self.desc_bar = QFrame()
        self.desc_bar.setStyleSheet(f"background: {GREEN}; border-radius: 3px;")
        self.desc_bar.setFixedHeight(14)
        self.bar_layout.addWidget(self.climb_bar, 33)
        self.bar_layout.addWidget(self.flat_bar, 34)
        self.bar_layout.addWidget(self.desc_bar, 33)
        layout.addLayout(self.bar_layout)

        lbl_layout = QHBoxLayout()
        lbl_layout.setSpacing(4)
        self.climb_lbl = QLabel("\u2191 \u2014")
        self.climb_lbl.setStyleSheet(f"color: {ORANGE}; font-size: 10px;")
        self.flat_lbl = QLabel("\u2192 \u2014")
        self.flat_lbl.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
        self.desc_lbl = QLabel("\u2193 \u2014")
        self.desc_lbl.setStyleSheet(f"color: {GREEN}; font-size: 10px;")
        lbl_layout.addWidget(self.climb_lbl)
        lbl_layout.addWidget(self.flat_lbl)
        lbl_layout.addWidget(self.desc_lbl)
        layout.addLayout(lbl_layout)

    def update_profile(self, climb_km, flat_km, desc_km):
        total = climb_km + flat_km + desc_km
        if total < 0.1:
            cp = fp = dp = 33.3
        else:
            cp = climb_km / total * 100
            dp = desc_km / total * 100
            fp = 100 - cp - dp
        self.bar_layout.setStretch(0, max(1, int(cp * 10)))
        self.bar_layout.setStretch(1, max(1, int(fp * 10)))
        self.bar_layout.setStretch(2, max(1, int(dp * 10)))
        self.climb_lbl.setText(f"\u2191 {cp:.0f}% ({climb_km:.1f} km)")
        self.flat_lbl.setText(f"\u2192 {fp:.0f}% ({flat_km:.1f} km)")
        self.desc_lbl.setText(f"\u2193 {dp:.0f}% ({desc_km:.1f} km)")


class _PlotInteractionMixin:
    """Locks pan/zoom and provides a vertical hover line tracking the cursor."""

    def _lock_view(self):
        vb = self.plot_widget.getViewBox()
        vb.setMouseEnabled(x=False, y=False)
        vb.setMenuEnabled(False)
        self.plot_widget.setMenuEnabled(False)
        self.plot_widget.wheelEvent = lambda ev: None

    def _add_hover_line(self):
        line = pg.InfiniteLine(angle=90, pen=pg.mkPen(MUTED, width=1, style=Qt.DashLine))
        line.setVisible(False)
        self.plot_widget.addItem(line, ignoreBounds=True)
        self.plot_widget.scene().sigMouseMoved.connect(self._on_mouse_moved)
        return line

    def _on_mouse_moved(self, pos):
        if not self.plot_widget.sceneBoundingRect().contains(pos):
            self.hover_line.setVisible(False)
            return
        mp = self.plot_widget.getViewBox().mapSceneToView(pos)
        self.hover_line.setPos(mp.x())
        self.hover_line.setVisible(True)


class ElevationPlot(_PlotInteractionMixin, QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumHeight(160)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        if HAS_PG:
            pg.setConfigOptions(antialias=True, background=CARD, foreground=TEXT)
            self.plot_widget = pg.PlotWidget()
            self.plot_widget.setLabel('left',   'Elevation', units='m', color=MUTED)
            self.plot_widget.setLabel('bottom', 'Distance',  units='km', color=MUTED)
            self.plot_widget.getAxis('left').setPen(pg.mkPen(MUTED))
            self.plot_widget.getAxis('bottom').setPen(pg.mkPen(MUTED))
            self.plot_widget.showGrid(x=True, y=True, alpha=0.15)
            self.curve = self.plot_widget.plot(pen=pg.mkPen(ACCENT, width=2))
            self.fill_items = []  # List of per-segment fills
            # Reserve (W'bal) overlay on a linked right-hand axis.
            self.reserve_vb = pg.ViewBox()
            self.plot_widget.showAxis('right')
            self.plot_widget.setLabel('right', 'Reserve', units='kJ', color=MUTED)
            self.plot_widget.getAxis('right').setPen(pg.mkPen(MUTED))
            self.plot_widget.scene().addItem(self.reserve_vb)
            self.plot_widget.getAxis('right').linkToView(self.reserve_vb)
            self.reserve_vb.setXLink(self.plot_widget.getViewBox())
            self.reserve_vb.setMouseEnabled(x=False, y=False)
            self.reserve_curve = pg.PlotCurveItem(pen=pg.mkPen(GREEN, width=1.5))
            self.reserve_vb.addItem(self.reserve_curve)
            self.warn_line = None
            self.plot_widget.getViewBox().sigResized.connect(self._sync_reserve_vb)
            self._lock_view()
            self.hover_line = self._add_hover_line()
            self._insp = None
            self.hover_label = pg.TextItem(anchor=(0, 1), color=TEXT, fill=pg.mkBrush(CARD))
            self.hover_label.setVisible(False)
            self.plot_widget.addItem(self.hover_label, ignoreBounds=True)
            layout.addWidget(self.plot_widget)
        else:
            layout.addWidget(QLabel("Install pyqtgraph for elevation plot"))

    def _sync_reserve_vb(self):
        self.reserve_vb.setGeometry(self.plot_widget.getViewBox().sceneBoundingRect())

    def _grade_to_color(self, grade):
        """Map grade (dimensionless) to fill color."""
        if grade < -0.02:
            return GREEN + "30"
        elif grade < 0.02:
            return MUTED + "30"
        elif grade < 0.05:
            return ORANGE + "20"
        elif grade < 0.08:
            return ORANGE + "30"
        else:
            return RED_COL + "30"

    def _grade_to_band(self, grade):
        """Map grade to a band index for grouping."""
        if grade < -0.02:
            return 0
        elif grade < 0.02:
            return 1
        elif grade < 0.05:
            return 2
        elif grade < 0.08:
            return 3
        else:
            return 4

    def update_data(self, distances_m, altitudes, grades=None):
        if not HAS_PG:
            return
        
        # Remove old fills and helper curves
        for item in self.fill_items:
            self.plot_widget.removeItem(item)
        self.fill_items = []
        
        dist_km = [d / 1000 for d in distances_m]
        self.curve.setData(dist_km, altitudes)
        self._alt_range = (min(altitudes), max(altitudes))
        
        # If grades provided, create grouped fills (batch consecutive same-band segments)
        if grades is not None and len(grades) > 0:
            baseline_y = min(altitudes)
            n_segs = len(grades)
            
            # Group consecutive segments with same color band
            i = 0
            while i < n_segs:
                band = self._grade_to_band(grades[i])
                j = i + 1
                while j < n_segs and self._grade_to_band(grades[j]) == band:
                    j += 1
                
                # Create one fill for segments i..j
                end_idx = min(j, len(dist_km) - 1)
                xs = dist_km[i:end_idx + 1]
                ys = altitudes[i:end_idx + 1]
                
                if len(xs) >= 2:
                    # Use PlotCurveItem directly to avoid PlotItem metadata issues
                    seg_curve = pg.PlotCurveItem(xs, ys)
                    base_curve = pg.PlotCurveItem([xs[0], xs[-1]], [baseline_y, baseline_y])
                    color = self._grade_to_color(grades[i])
                    fill = pg.FillBetweenItem(
                        seg_curve, base_curve,
                        brush=pg.mkBrush(color)
                    )
                    self.plot_widget.addItem(fill)
                    self.fill_items.append(fill)
                
                i = j
        else:
            # Fallback: single fill if no grades provided
            base_curve = pg.PlotCurveItem(
                [dist_km[0], dist_km[-1]],
                [min(altitudes), min(altitudes)]
            )
            fill = pg.FillBetweenItem(
                self.curve,
                base_curve,
                brush=pg.mkBrush(ACCENT + "30")
            )
            self.plot_widget.addItem(fill)
            self.fill_items.append(fill)

    def set_inspector_data(self, distances, sim_result, warn_j=0.0):
        """Overlay reserve (W'bal) on a right axis and cache hover metrics."""
        if not HAS_PG:
            return

        if distances is None or len(distances) == 0 or sim_result is None:
            self._insp = None
            self.hover_label.setVisible(False)
            self.reserve_curve.setData([], [])
            if self.warn_line is not None:
                self.reserve_vb.removeItem(self.warn_line)
                self.warn_line = None
            return

        # Cache per-segment metrics for hover readout.
        self._insp = {
            'dist_km': [d / 1000 for d in distances],
            'grade': sim_result.get('seg_grade', sim_result.get('grades', [])),
            'speed': sim_result.get('seg_speed', []),
            'power': sim_result.get('seg_power', []),
        }

        seg_wbal = sim_result.get('seg_wbal', [])
        if len(seg_wbal) == 0:
            self.reserve_curve.setData([], [])
            return

        n = min(len(distances), len(seg_wbal))
        dist_km = [d / 1000 for d in distances[:n]]
        wbal_kj = [w / 1000 for w in seg_wbal[:n]]
        self.reserve_curve.setData(dist_km, wbal_kj)

        if self.warn_line is not None:
            self.reserve_vb.removeItem(self.warn_line)
            self.warn_line = None
        if warn_j > 0:
            self.warn_line = pg.InfiniteLine(
                pos=warn_j / 1000, angle=0,
                pen=pg.mkPen(ORANGE, width=1, style=Qt.DashLine)
            )
            self.reserve_vb.addItem(self.warn_line)
        self._sync_reserve_vb()

    def _on_mouse_moved(self, pos):
        inside = self.plot_widget.sceneBoundingRect().contains(pos)
        if not inside:
            self.hover_line.setVisible(False)
            self.hover_label.setVisible(False)
            return
        mp = self.plot_widget.getViewBox().mapSceneToView(pos)
        x = mp.x()
        self.hover_line.setPos(x)
        self.hover_line.setVisible(True)
        if not self._insp or not self._insp['dist_km']:
            self.hover_label.setVisible(False)
            return
        dist_km = self._insp['dist_km']
        i = min(range(len(dist_km)), key=lambda k: abs(dist_km[k] - x))
        grade = self._insp['grade'][i] if i < len(self._insp['grade']) else 0.0
        speed = self._insp['speed'][i] if i < len(self._insp['speed']) else 0.0
        power = self._insp['power'][i] if i < len(self._insp['power']) else 0.0
        self.hover_label.setText(
            f"{dist_km[i]:.1f} km   {grade * 100:+.1f}%   "
            f"{speed * 3.6:.0f} km/h   {power:.0f} W"
        )
        self.hover_label.setPos(x, mp.y())
        self.hover_label.setVisible(True)
