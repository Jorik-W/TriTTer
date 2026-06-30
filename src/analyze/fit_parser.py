"""FIT file parser for bike ride data.

Compatibility shim — delegates to ``core/fit_loader.py``.
The returned DataFrame is identical to the previous implementation so all
downstream Analyse code (qt_gui, cli, analyzer) works without changes.
"""

import pandas as pd
import numpy as np
import logging

from fit_loader import load_course_file


class FITParser:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.elevation_source = "FIT file"

    def parse_fit_file(self, file_path, status_callback=None):
        """Load *file_path* and return a DataFrame compatible with the Analyse pipeline."""
        course = load_course_file(file_path, status_callback=status_callback)
        if not course.valid:
            raise ValueError(course.error or "Failed to load file")

        n = len(course.distances)
        data = {
            "timestamp":  course.timestamps,
            "latitude":   course.latitudes,
            "longitude":  course.longitudes,
            "altitude":   course.altitudes,
            "distance":   course.distances,
            "speed":      course.speed      if course.speed      else [None] * n,
            "power":      course.power      if course.power      else [None] * n,
            "heart_rate": course.heart_rate if course.heart_rate else [None] * n,
            "cadence":    course.cadence    if course.cadence    else [None] * n,
        }
        df = pd.DataFrame(data)

        # Convert timestamps
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")

        # Preserve original FIT altitude for elevation-source comparisons
        if "altitude" in df.columns:
            df["altitude_fit"] = df["altitude"].copy()

        # Forward/back-fill non-critical channels
        for col in ["altitude", "heart_rate", "cadence", "distance"]:
            if col in df.columns:
                df[col] = df[col].ffill().bfill()

        return df
