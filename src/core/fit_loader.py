"""Unified FIT / GPX file loader for TriTTer.

Reads a FIT or GPX file *once* into a :class:`CourseFile` that carries every
field either Analyse or Plan might need.  Fields not present in the source file
are set to ``None``.

Usage::

    from fit_loader import load_course_file, capability_check

    course = load_course_file("/path/to/ride.fit")
    caps = capability_check(course)
    # caps.analyse_ok  -> bool
    # caps.plan_ok     -> bool
    # caps.message     -> human-readable string if something is missing

Both ``analyze/fit_parser.FITParser`` and ``plan/course.parse_course_file`` are
thin compatibility shims that delegate here.
"""

import os
import math
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List

import numpy as np

_log = logging.getLogger(__name__)

_SEMICIRCLE_TO_DEG = 180.0 / (2 ** 31)
_EARTH_R = 6_371_000.0  # metres

try:
    from fitparse import FitFile as _FitFile
    _HAS_FITPARSE = True
except ImportError:
    _HAS_FITPARSE = False
    _FitFile = None  # type: ignore


# ---------------------------------------------------------------------------
# Data container
# ---------------------------------------------------------------------------

@dataclass
class CourseFile:
    """Unified course data.  All per-point lists are index-aligned.

    Fields that the source file does not provide are ``None`` (scalars) or an
    empty list (vectors).
    """
    # source info
    path: str = ""
    file_type: str = ""           # "fit" | "gpx"
    error: Optional[str] = None   # set when valid=False

    # per-point time series (all same length, or empty/None)
    timestamps: List = field(default_factory=list)   # datetime | None per point
    latitudes:  List[Optional[float]] = field(default_factory=list)
    longitudes: List[Optional[float]] = field(default_factory=list)
    altitudes:  List[Optional[float]] = field(default_factory=list)
    distances:  List[float] = field(default_factory=list)  # metres, cumulative

    # channels present only in FIT power-meter files
    speed:      List[Optional[float]] = field(default_factory=list)   # m/s
    power:      List[Optional[float]] = field(default_factory=list)   # W
    heart_rate: List[Optional[float]] = field(default_factory=list)
    cadence:    List[Optional[float]] = field(default_factory=list)
    temperature:List[Optional[float]] = field(default_factory=list)

    # derived geometry (computed from distances + altitudes, always filled on success)
    grades:     List[float] = field(default_factory=list)  # len = n_points - 1
    altitudes_smooth: List[float] = field(default_factory=list)

    # summary scalars
    total_distance: float = 0.0   # metres
    total_elevation: float = 0.0  # metres gain
    mean_climb_grad: float = 0.0
    mean_desc_grad: float = 0.0
    start_time: Optional[datetime] = None

    @property
    def valid(self) -> bool:
        return self.error is None and len(self.distances) >= 10

    @property
    def has_power(self) -> bool:
        return any(v is not None and v > 0 for v in self.power)

    @property
    def has_speed(self) -> bool:
        return any(v is not None and v > 0 for v in self.speed)

    @property
    def has_gps(self) -> bool:
        return any(v is not None for v in self.latitudes)


@dataclass
class Capabilities:
    analyse_ok: bool
    plan_ok: bool
    message: str = ""   # non-empty when something is unavailable / gated


def capability_check(course: CourseFile) -> Capabilities:
    """Return which modes are usable and a human-readable status message."""
    if not course.valid:
        return Capabilities(
            analyse_ok=False,
            plan_ok=False,
            message=f"Could not load file: {course.error}",
        )

    plan_ok = len(course.distances) >= 10
    analyse_ok = course.has_power and course.has_speed

    parts = []
    if not analyse_ok:
        missing = []
        if not course.has_power:
            missing.append("power")
        if not course.has_speed:
            missing.append("speed")
        parts.append(
            f"CdA analysis needs {' and '.join(missing)} data — "
            f"{'this GPX file has route geometry only' if course.file_type == 'gpx' else 'not found in this FIT file'}. "
            "Plan mode available."
        )
    msg = "  ".join(parts) if parts else ""
    return Capabilities(analyse_ok=analyse_ok, plan_ok=plan_ok, message=msg)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def load_course_file(path: str, status_callback=None) -> CourseFile:
    """Load a FIT or GPX file into a :class:`CourseFile`.

    ``status_callback(msg: str)`` is called with progress strings if provided.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext == ".gpx":
        return _load_gpx(path, status_callback)
    return _load_fit(path, status_callback)


# ---------------------------------------------------------------------------
# FIT loader
# ---------------------------------------------------------------------------

def _load_fit(path: str, status_callback=None) -> CourseFile:
    course = CourseFile(path=path, file_type="fit")

    if not _HAS_FITPARSE:
        course.error = "fitparse library not installed"
        return course

    try:
        if status_callback:
            status_callback("Parsing FIT file…")

        fit = _FitFile(path)
        records = []
        for rec in fit.get_messages("record"):
            d = {}
            for f in rec:
                if f.value is not None:
                    d[f.name] = f.value
            records.append(d)

        if not records:
            course.error = "No record messages found in FIT file"
            return course

        # ------ extract per-point arrays ----------------------------------
        timestamps, latitudes, longitudes, altitudes, distances = [], [], [], [], []
        speed, power, heart_rate, cadence, temperature = [], [], [], [], []
        last_lat = last_lon = None

        for r in records:
            # altitude: prefer enhanced_altitude
            alt = r.get("enhanced_altitude") or r.get("altitude")

            # distance
            dist = r.get("distance")
            if dist is None or alt is None:
                continue

            # GPS: FIT stores as int32 semicircles
            lat_raw = r.get("position_lat")
            lon_raw = r.get("position_long")
            if lat_raw is not None and lon_raw is not None:
                last_lat = float(lat_raw) * _SEMICIRCLE_TO_DEG
                last_lon = float(lon_raw) * _SEMICIRCLE_TO_DEG

            timestamps.append(r.get("timestamp"))
            latitudes.append(last_lat)
            longitudes.append(last_lon)
            altitudes.append(float(alt))
            distances.append(float(dist))

            # optional channels
            spd = r.get("speed")
            if spd is not None and spd > 50:   # mm/s → m/s
                spd = spd / 1000.0
            speed.append(float(spd) if spd is not None else None)
            power.append(float(r["power"]) if "power" in r else None)
            heart_rate.append(float(r["heart_rate"]) if "heart_rate" in r else None)
            cadence.append(float(r["cadence"]) if "cadence" in r else None)
            temperature.append(float(r["temperature"]) if "temperature" in r else None)

        if len(distances) < 10:
            course.error = "Not enough GPS data points in FIT file"
            return course

        # backfill leading GPS nones
        _backfill_leading(latitudes)
        _backfill_leading(longitudes)

        # forward-fill non-critical channels
        _ffill(altitudes)

        course.timestamps  = timestamps
        course.latitudes   = latitudes
        course.longitudes  = longitudes
        course.altitudes   = altitudes
        course.distances   = distances
        course.speed       = speed
        course.power       = power
        course.heart_rate  = heart_rate
        course.cadence     = cadence
        course.temperature = temperature

        # pick start time: use first point where speed > 0 for 3+ continuous minutes
        _MIN_MOVING_SECONDS = 180.0
        start_idx = None
        if any(s is not None and s > 0 for s in speed):
            _mov_start_idx = None
            _mov_start_ts  = None
            for _i, (_ts, _spd) in enumerate(zip(timestamps, speed)):
                if _ts is None:
                    _mov_start_idx = None
                    _mov_start_ts  = None
                    continue
                if _spd is not None and _spd > 0:
                    if _mov_start_idx is None:
                        _mov_start_idx = _i
                        _mov_start_ts  = _ts
                    else:
                        try:
                            if (_ts - _mov_start_ts).total_seconds() >= _MIN_MOVING_SECONDS:
                                start_idx = _mov_start_idx
                                break
                        except (TypeError, AttributeError):
                            _mov_start_idx = _i
                            _mov_start_ts  = _ts
                else:
                    _mov_start_idx = None
                    _mov_start_ts  = None

        # Fall back to first non-None timestamp
        _first_ts_idx = next((i for i, t in enumerate(timestamps) if t is not None), None)
        if start_idx is None:
            start_idx = _first_ts_idx

        if start_idx is not None:
            t = timestamps[start_idx]
            course.start_time = t if isinstance(t, datetime) else getattr(t, "replace", lambda **k: t)()

        _compute_derived(course)
        return course

    except Exception as e:
        _log.exception("Error loading FIT file %s", path)
        course.error = str(e)
        return course


# ---------------------------------------------------------------------------
# GPX loader
# ---------------------------------------------------------------------------

def _parse_gpx_time(text):
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def _load_gpx(path: str, status_callback=None) -> CourseFile:
    course = CourseFile(path=path, file_type="gpx")
    try:
        if status_callback:
            status_callback("Parsing GPX file…")

        root = ET.parse(path).getroot()
        trackpoints = root.findall(".//{*}trkpt")
        if len(trackpoints) < 10:
            course.error = "Not enough track points in GPX file"
            return course

        timestamps, latitudes, longitudes, altitudes, distances = [], [], [], [], []
        prev_lat = prev_lon = None
        cumulative_dist = 0.0

        for trkpt in trackpoints:
            lat = trkpt.get("lat")
            lon = trkpt.get("lon")
            ele_el = trkpt.find("{*}ele")
            if lat is None or lon is None or ele_el is None or ele_el.text is None:
                continue

            lat = float(lat)
            lon = float(lon)
            alt = float(ele_el.text)

            if prev_lat is not None:
                cumulative_dist += _haversine(prev_lat, prev_lon, lat, lon)

            time_el = trkpt.find("{*}time")
            timestamps.append(_parse_gpx_time(time_el.text) if time_el is not None else None)
            latitudes.append(lat)
            longitudes.append(lon)
            altitudes.append(alt)
            distances.append(cumulative_dist)
            prev_lat, prev_lon = lat, lon

        if len(distances) < 10:
            course.error = "Not enough usable track points in GPX file"
            return course

        course.timestamps = timestamps
        course.latitudes  = latitudes
        course.longitudes = longitudes
        course.altitudes  = altitudes
        course.distances  = distances
        # GPX has no power/speed/hr/cadence — leave as empty lists
        course.start_time = next((t for t in timestamps if t is not None), None)

        _compute_derived(course)
        return course

    except Exception as e:
        _log.exception("Error loading GPX file %s", path)
        course.error = str(e)
        return course


# ---------------------------------------------------------------------------
# Derived geometry (grades, smoothed altitude, summary stats)
# ---------------------------------------------------------------------------

def _compute_derived(course: CourseFile):
    """Populate grades, altitudes_smooth and summary scalars in-place."""
    alts = np.array(course.altitudes, dtype=float)
    dists = np.array(course.distances, dtype=float)

    alts = _sanitize_edge_altitudes(alts)

    # Smooth altitude to reduce GPS noise
    window = min(15, len(alts) // 10)
    if window > 1:
        if window % 2 == 0:
            window += 1
        kernel = np.ones(window) / window
        pad = window // 2
        padded = np.pad(alts, (pad, pad), mode="edge")
        alts_smooth = np.convolve(padded, kernel, mode="valid")
    else:
        alts_smooth = alts.copy()

    d_alt  = np.diff(alts_smooth)
    d_dist = np.diff(dists)
    d_dist = np.where(d_dist < 0.1, 0.1, d_dist)
    grades = d_alt / d_dist

    climb_mask = grades > 0.005
    desc_mask  = grades < -0.005
    total_gain = float(np.sum(d_alt[d_alt > 0]))
    climb_dist = float(np.sum(d_dist[climb_mask])) or 1.0
    desc_dist  = float(np.sum(d_dist[desc_mask]))  or 1.0

    course.altitudes_smooth = alts_smooth.tolist()
    course.grades           = grades.tolist()
    course.total_distance   = float(dists[-1])
    course.total_elevation  = total_gain
    course.mean_climb_grad  = max(0.01, min(0.20, total_gain / climb_dist))
    course.mean_desc_grad   = max(0.01, min(0.20,
        float(abs(np.sum(d_alt[d_alt < 0]))) / desc_dist))


# ---------------------------------------------------------------------------
# Compatibility helpers used by old shim callers
# ---------------------------------------------------------------------------

def course_to_plan_dict(course: CourseFile) -> dict:
    """Convert a CourseFile into the dict format expected by plan/course consumers."""
    if not course.valid:
        return {"valid": False, "error": course.error}
    return {
        "valid": True,
        "total_distance": course.total_distance,
        "total_elevation": course.total_elevation,
        "mean_climb_grad": course.mean_climb_grad,
        "mean_desc_grad": course.mean_desc_grad,
        "distances": course.distances,
        "altitudes": course.altitudes_smooth,
        "grades": course.grades,
        "latitudes": course.latitudes if course.has_gps else None,
        "longitudes": course.longitudes if course.has_gps else None,
        "timestamps": course.timestamps if any(t is not None for t in course.timestamps) else None,
        "has_timestamps": any(t is not None for t in course.timestamps),
    }


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _haversine(lat1, lon1, lat2, lon2) -> float:
    r1, r2 = math.radians(lat1), math.radians(lat2)
    dr = r2 - r1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dr / 2) ** 2 + math.cos(r1) * math.cos(r2) * math.sin(dl / 2) ** 2
    return _EARTH_R * 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(1e-12, 1.0 - a)))


def _backfill_leading(lst):
    first = next((v for v in lst if v is not None), None)
    for i, v in enumerate(lst):
        if v is None:
            lst[i] = first
        else:
            break


def _ffill(lst):
    last = None
    for i, v in enumerate(lst):
        if v is not None:
            last = v
        elif last is not None:
            lst[i] = last


def _sanitize_edge_altitudes(altitudes: np.ndarray) -> np.ndarray:
    n = len(altitudes)
    if n < 8:
        return altitudes
    window = min(20, max(5, n // 20))
    head_ref = float(np.nanmedian(altitudes[1: 1 + window]))
    tail_ref  = float(np.nanmedian(altitudes[-1 - window: -1]))

    def _suspicious(edge, ref):
        return (abs(edge) <= 1.0 and abs(ref) >= 10.0 and abs(edge - ref) >= 15.0) \
               or abs(edge - ref) >= 120.0

    if _suspicious(float(altitudes[0]), head_ref):
        altitudes[0] = head_ref
    if _suspicious(float(altitudes[-1]), tail_ref):
        altitudes[-1] = tail_ref
    return altitudes
