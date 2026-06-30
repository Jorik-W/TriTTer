"""Rider profile store for TriTTer.

The Profile is the single source of truth for rider-specific parameters
(mass, CdA, Crr, wind handling, FTP, ...). A profile (rider) is *selected*
in the Analyze and Plan tabs; both modes read their rider parameters from
the selected profile.

Profiles persist to JSON (``~/.tritter/profiles.json``) and auto-load on
startup. On first run the store is seeded with the riders documented in the
original cda_analyzer config (measured CdA values from real ride analysis).
"""

import os
import json
import logging
from dataclasses import dataclass, asdict, field

_logger = logging.getLogger(__name__)

PROFILE_DIR = os.path.join(os.path.expanduser("~"), ".tritter")
PROFILE_PATH = os.path.join(PROFILE_DIR, "profiles.json")


@dataclass
class Rider:
    """A single rider profile. Fields cover both Analyze and Plan modes."""
    name: str = "New Rider"
    rider_mass: float = 75.0          # kg
    bike_mass: float = 10.0           # kg
    rolling_resistance: float = 0.004  # Crr
    drivetrain_loss: float = 0.025     # fraction (1 - efficiency)
    cda: float = 0.30                  # m^2 (measured in Analyze or manual)
    climbing_cda: float = 0.32         # m^2 (used by Plan on climbs)
    ftp: float = 250.0                 # W
    max_power: float = 400.0           # W (used by Plan durability model)
    reserve_kj: float = 20.0           # kJ  (W' reserve capacity)
    min_reserve_kj: float = 15.0       # kJ  (W' min reserve warning)
    reserve_decay: float = 0.20        # fraction (W' decay per kJ accumulated)
    notes: str = ""

    @property
    def total_mass(self):
        return float(self.rider_mass) + float(self.bike_mass)

    @property
    def efficiency(self):
        return 1.0 - float(self.drivetrain_loss)

    def to_analyze_overrides(self):
        """Rider params that override the Analyze (CdA) parameter set."""
        return {
            "rider_mass": float(self.rider_mass),
            "bike_mass": float(self.bike_mass),
            "rolling_resistance": float(self.rolling_resistance),
            "drivetrain_loss": float(self.drivetrain_loss),
        }

    def to_plan_overrides(self):
        """Rider params used by the Plan (pacing) side."""
        return {
            "cda": float(self.cda),
            "climbing_cda": float(self.climbing_cda),
            "mass": float(self.total_mass),
            "crr": float(self.rolling_resistance),
            "eff": float(self.efficiency),
            "ftp": float(self.ftp),
            "max_power": float(self.max_power),
            "reserve_kj": float(self.reserve_kj),
            "min_reserve_kj": float(self.min_reserve_kj),
            "reserve_decay": float(self.reserve_decay),
        }
class ProfileStore:
    """Loads, saves and manages rider profiles with a selected-rider pointer."""

    def __init__(self, path=PROFILE_PATH):
        self.path = path
        self.riders = []
        self.selected = None  # rider name
        self.load()

    # ---- persistence ---------------------------------------------------
    def load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                self.riders = [Rider(**r) for r in data.get("riders", [])]
                self.selected = data.get("selected")
            except Exception:
                _logger.exception("Failed to read %s; reseeding profiles", self.path)

        if self.selected not in self.names():
            self.selected = self.names()[0]
        return self

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            payload = {
                "selected": self.selected,
                "riders": [asdict(r) for r in self.riders],
            }
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
        except Exception:
            _logger.exception("Failed to save profiles to %s", self.path)

    # ---- access --------------------------------------------------------
    def names(self):
        return [r.name for r in self.riders]

    def get(self, name):
        for r in self.riders:
            if r.name == name:
                return r
        return None

    def get_selected(self):
        return self.get(self.selected) if self.selected else None

    def select(self, name):
        if name in self.names():
            self.selected = name
            self.save()
        return self.get_selected()

    def add(self, rider):
        # Ensure unique name.
        base = rider.name or "New Rider"
        name = base
        i = 2
        while name in self.names():
            name = f"{base} ({i})"
            i += 1
        rider.name = name
        self.riders.append(rider)
        self.selected = rider.name
        self.save()
        return rider

    def update(self, rider):
        for i, r in enumerate(self.riders):
            if r.name == rider.name:
                self.riders[i] = rider
                self.save()
                return rider
        return self.add(rider)

    def remove(self, name):
        self.riders = [r for r in self.riders if r.name != name]
        if self.selected not in self.names():
            self.selected = self.names()[0]
        self.save()
