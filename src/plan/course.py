"""
Course file parsing and gradient band analysis.
"""

import os
import math
import xml.etree.ElementTree as ET
from datetime import datetime

import numpy as np

from physics import solve_speed, estimate_time, RHO

# Semicircles -> degrees (FIT stores lat/lon as int32 semicircles).
_SEMICIRCLE_TO_DEG = 180.0 / (2 ** 31)

try:
    from fitparse import FitFile
    HAS_FITPARSE = True
except ImportError:
    HAS_FITPARSE = False


def segment_bearings(latitudes, longitudes):
    """Initial great-circle bearing (deg, 0=N, clockwise) for each segment.

    Returns an array of length ``n_points - 1`` aligned to course segments.
    Points with missing coordinates carry the previous valid bearing (or 0).
    """
    lat = np.asarray(latitudes, dtype=float)
    lon = np.asarray(longitudes, dtype=float)
    n = min(len(lat), len(lon))
    if n < 2:
        return np.zeros(max(0, n - 1), dtype=float)

    bearings = np.zeros(n - 1, dtype=float)
    last = 0.0
    for i in range(n - 1):
        lat1, lon1, lat2, lon2 = lat[i], lon[i], lat[i + 1], lon[i + 1]
        if not (np.isfinite(lat1) and np.isfinite(lon1)
                and np.isfinite(lat2) and np.isfinite(lon2)):
            bearings[i] = last
            continue
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        dlon = math.radians(lon2 - lon1)
        x = math.sin(dlon) * math.cos(phi2)
        y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlon)
        brng = math.degrees(math.atan2(x, y)) % 360.0
        bearings[i] = brng
        last = brng
    return bearings


def project_headwind(bearings, wind_from_deg, wind_speed_ms):
    """Per-segment headwind component (m/s) along travel direction.

    ``wind_from_deg`` is the meteorological direction the wind blows *from*
    (0 = North). Headwind is maximal when the travel bearing equals the
    wind-source direction; a tailwind yields a negative component.
    """
    bearings = np.asarray(bearings, dtype=float)
    speed = float(wind_speed_ms)
    rel = np.radians(bearings - float(wind_from_deg))
    return speed * np.cos(rel)


def route_headwind(target_distances, route_distances, latitudes, longitudes,
                   wind_from_deg, wind_speed_ms):
    """Per-segment headwind (m/s) for an arbitrary distance grid.

    Bearings are computed from the full-resolution ``latitudes``/``longitudes``
    and resampled onto ``target_distances`` by nearest segment midpoint, so the
    result stays aligned with grids that were downsampled independently of the
    coordinate arrays. Falls back to a scalar projection (assumed due-North
    heading) when no usable geometry is available.
    """
    speed = float(wind_speed_ms)
    if speed <= 1e-9:
        return 0.0

    if latitudes is None or longitudes is None or route_distances is None:
        return speed * math.cos(math.radians(float(wind_from_deg)))

    bearings = segment_bearings(latitudes, longitudes)
    td = np.asarray(target_distances, dtype=float)
    if len(bearings) == 0 or len(td) < 2:
        return speed * math.cos(math.radians(float(wind_from_deg)))

    head_full = project_headwind(bearings, wind_from_deg, speed)
    rd = np.asarray(route_distances, dtype=float)
    seg_mid = 0.5 * (rd[:-1] + rd[1:])
    tgt_mid = 0.5 * (td[:-1] + td[1:])
    if len(seg_mid) == 0:
        return speed * math.cos(math.radians(float(wind_from_deg)))
    idx = np.clip(np.searchsorted(seg_mid, tgt_mid), 0, len(head_full) - 1)
    return head_full[idx]




def downsample_course(grades, distances, target_n=1000):
    """
    Downsample course segments to target_n segments by merging consecutive segments.
    
    Preserves total distance and elevation. Returns (downsampled_grades, downsampled_distances).
    """
    grades = np.array(grades, dtype=float)
    distances = np.array(distances, dtype=float)

    n = min(len(grades), len(distances) - 1)
    if n <= 0:
        return np.array([], dtype=float), np.array([], dtype=float)
    grades = grades[:n]
    distances = distances[:n + 1]

    if n <= target_n:
        return grades, distances

    start = float(distances[0])
    end = float(distances[-1])
    if end <= start:
        return grades, distances

    # Build equal-distance bins so preview work scales predictably with route length.
    bin_edges = np.linspace(start, end, target_n + 1)
    ds_grades = np.zeros(target_n, dtype=float)
    seg_idx = 0

    for i in range(target_n):
        b0 = bin_edges[i]
        b1 = bin_edges[i + 1]
        elev = 0.0
        while seg_idx < n and distances[seg_idx + 1] <= b0:
            seg_idx += 1

        j = seg_idx
        while j < n and distances[j] < b1:
            left = max(b0, float(distances[j]))
            right = min(b1, float(distances[j + 1]))
            overlap = max(0.0, right - left)
            if overlap > 0:
                elev += float(grades[j]) * overlap
            if distances[j + 1] <= b1:
                j += 1
            else:
                break

        bin_dist = b1 - b0
        ds_grades[i] = elev / bin_dist if bin_dist > 1e-9 else 0.0

    return ds_grades, bin_edges


def _sanitize_edge_altitudes(altitudes):
    """Repair suspicious first/last altitude sentinel values without touching interior points."""
    altitudes = np.array(altitudes, dtype=float)
    n = len(altitudes)
    if n < 8:
        return altitudes

    window = min(20, max(5, n // 20))
    head_ref = float(np.median(altitudes[1:1 + window]))
    tail_ref = float(np.median(altitudes[-1 - window:-1]))

    def _is_suspicious(edge, ref):
        diff = abs(edge - ref)
        near_zero = abs(edge) <= 1.0
        # Common importer corruption: edge sample pinned to 0 despite stable nearby terrain.
        return (near_zero and abs(ref) >= 10.0 and diff >= 15.0) or diff >= 120.0

    if _is_suspicious(float(altitudes[0]), head_ref):
        altitudes[0] = head_ref
    if _is_suspicious(float(altitudes[-1]), tail_ref):
        altitudes[-1] = tail_ref

    return altitudes


def _profile_stats_from_distances_and_altitudes(
    distances, altitudes, latitudes=None, longitudes=None, timestamps=None
):
    """Build a smoothed course profile and summary stats from raw samples.

    Optional ``latitudes``/``longitudes``/``timestamps`` are passed through
    index-aligned with ``distances`` so downstream weather sampling can locate
    each point in space and (for history mode) time. They are omitted from the
    result when not supplied.
    """
    if len(distances) < 10 or len(altitudes) < 10:
        return {'valid': False, 'error': 'Not enough course data points in file'}

    distances = np.array(distances, dtype=float)
    altitudes = np.array(altitudes, dtype=float)
    altitudes = _sanitize_edge_altitudes(altitudes)

    # Smooth altitude with a rolling window to reduce GPS noise
    window = min(15, len(altitudes) // 10)
    if window > 1:
        if window % 2 == 0:
            window += 1
        kernel = np.ones(window) / window
        # Pad using edge values to avoid introducing artificial dips at start/end.
        pad = window // 2
        padded = np.pad(altitudes, (pad, pad), mode='edge')
        altitudes_smooth = np.convolve(padded, kernel, mode='valid')
    else:
        altitudes_smooth = altitudes

    # Compute elevation gain/loss and gradients
    d_alt = np.diff(altitudes_smooth)
    d_dist = np.diff(distances)
    d_dist = np.where(d_dist < 0.1, 0.1, d_dist)  # avoid division by zero

    grades = d_alt / d_dist  # dimensionless

    # Classify segments
    climb_mask = grades > 0.005   # >0.5% grade = climbing
    desc_mask = grades < -0.005   # <-0.5% grade = descending

    total_elev_gain = float(np.sum(d_alt[d_alt > 0]))
    total_elev_loss = float(abs(np.sum(d_alt[d_alt < 0])))
    total_dist = float(distances[-1])

    climb_dist = float(np.sum(d_dist[climb_mask])) if climb_mask.any() else 1.0
    desc_dist = float(np.sum(d_dist[desc_mask])) if desc_mask.any() else 1.0

    mean_climb_grad = (total_elev_gain / climb_dist) if climb_dist > 0 else 0.05
    mean_desc_grad = (total_elev_loss / desc_dist) if desc_dist > 0 else 0.05

    # Clamp to reasonable range
    mean_climb_grad = max(0.01, min(0.20, mean_climb_grad))
    mean_desc_grad = max(0.01, min(0.20, mean_desc_grad))

    result = {
        'valid': True,
        'total_distance': total_dist,
        'total_elevation': total_elev_gain,
        'mean_climb_grad': mean_climb_grad,
        'mean_desc_grad': mean_desc_grad,
        'distances': distances.tolist(),
        'altitudes': altitudes_smooth.tolist(),
        'grades': grades.tolist(),
    }

    if latitudes is not None and longitudes is not None:
        result['latitudes'] = [float(v) for v in latitudes]
        result['longitudes'] = [float(v) for v in longitudes]
    if timestamps is not None and any(t is not None for t in timestamps):
        result['timestamps'] = list(timestamps)
        result['has_timestamps'] = True
    else:
        result['has_timestamps'] = False

    return result


def parse_fit_file(path):
    """Extract elevation profile and distance from a FIT file."""
    if not HAS_FITPARSE:
        return {'valid': False, 'error': 'fitparse not installed'}

    try:
        fit = FitFile(path)
        records = []
        for rec in fit.get_messages('record'):
            d = {}
            for field in rec:
                d[field.name] = field.value
            records.append(d)

        if not records:
            return {'valid': False, 'error': 'No record messages found in FIT file'}

        distances = []
        altitudes = []
        latitudes = []
        longitudes = []
        timestamps = []
        last_lat = None
        last_lon = None
        for r in records:
            dist = r.get('distance')
            # Preserve valid 0.0 m values by checking None explicitly.
            alt = r.get('enhanced_altitude')
            if alt is None:
                alt = r.get('altitude')
            if dist is None or alt is None:
                continue

            lat_raw = r.get('position_lat')
            lon_raw = r.get('position_long')
            if lat_raw is not None and lon_raw is not None:
                # FIT stores position as int32 semicircles.
                last_lat = float(lat_raw) * _SEMICIRCLE_TO_DEG
                last_lon = float(lon_raw) * _SEMICIRCLE_TO_DEG

            distances.append(float(dist))
            altitudes.append(float(alt))
            latitudes.append(last_lat)
            longitudes.append(last_lon)
            timestamps.append(r.get('timestamp'))

        if len(distances) < 10:
            return {'valid': False, 'error': 'Not enough GPS data points in FIT file'}

        has_geo = any(v is not None for v in latitudes)
        if has_geo:
            # Backfill leading None positions (before first GPS fix) so weather
            # sampling always has usable coordinates.
            first_lat = next((v for v in latitudes if v is not None), None)
            first_lon = next((v for v in longitudes if v is not None), None)
            latitudes = [v if v is not None else first_lat for v in latitudes]
            longitudes = [v if v is not None else first_lon for v in longitudes]

        return _profile_stats_from_distances_and_altitudes(
            distances,
            altitudes,
            latitudes=latitudes if has_geo else None,
            longitudes=longitudes if has_geo else None,
            timestamps=timestamps,
        )

    except Exception as e:
        return {'valid': False, 'error': str(e)}


def _parse_gpx_time(text):
    """Parse a GPX ISO-8601 ``<time>`` value into a datetime, or None."""
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.strip().replace('Z', '+00:00'))
    except ValueError:
        return None


def parse_gpx_file(path):
    """Extract elevation profile and distance from a GPX file."""
    try:
        root = ET.parse(path).getroot()
        trackpoints = root.findall('.//{*}trkpt')
        if len(trackpoints) < 10:
            return {'valid': False, 'error': 'Not enough track points in GPX file'}

        distances = []
        altitudes = []
        latitudes = []
        longitudes = []
        timestamps = []
        prev_lat = None
        prev_lon = None
        cumulative_dist = 0.0

        for trkpt in trackpoints:
            lat = trkpt.get('lat')
            lon = trkpt.get('lon')
            ele_el = trkpt.find('{*}ele')
            if lat is None or lon is None or ele_el is None or ele_el.text is None:
                continue

            lat = float(lat)
            lon = float(lon)
            alt = float(ele_el.text)

            if prev_lat is not None and prev_lon is not None:
                lat1 = math.radians(prev_lat)
                lon1 = math.radians(prev_lon)
                lat2 = math.radians(lat)
                lon2 = math.radians(lon)
                dlat = lat2 - lat1
                dlon = lon2 - lon1
                a = math.sin(dlat / 2.0) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2
                c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(1e-12, 1.0 - a)))
                cumulative_dist += 6371000.0 * c

            time_el = trkpt.find('{*}time')
            time_val = _parse_gpx_time(time_el.text) if time_el is not None else None

            distances.append(cumulative_dist)
            altitudes.append(alt)
            latitudes.append(lat)
            longitudes.append(lon)
            timestamps.append(time_val)
            prev_lat = lat
            prev_lon = lon

        if len(distances) < 10:
            return {'valid': False, 'error': 'Not enough usable track points in GPX file'}

        return _profile_stats_from_distances_and_altitudes(
            distances,
            altitudes,
            latitudes=latitudes,
            longitudes=longitudes,
            timestamps=timestamps,
        )

    except Exception as e:
        return {'valid': False, 'error': str(e)}


def parse_course_file(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == '.gpx':
        return parse_gpx_file(path)
    return parse_fit_file(path)


# Gradient bands: (label, lower_bound_inclusive, upper_bound_exclusive)
GRADIENT_BANDS = [
    ("< -5%",   -999,  -0.05),
    ("-5 \u2013 -2%", -0.05, -0.02),
    ("-2 \u2013 0%",  -0.02,  0.00),
    ("0 \u2013 2%",    0.00,  0.02),
    ("2 \u2013 5%",    0.02,  0.05),
    ("5 \u2013 8%",    0.05,  0.08),
    ("8 \u2013 12%",   0.08,  0.12),
    ("> 12%",     0.12,  999),
]


def gradient_band_analysis(fit_data, params):
    """Compute distance and suggested power/speed per gradient band."""
    grades = fit_data.get('grades', [])
    distances = fit_data.get('distances', [])
    if len(grades) < 2 or len(distances) < 2:
        return None

    d_dist = np.diff(distances)
    eff = params['drivetrain_eff']
    cda = params['cda']
    climb_cda = params.get('climb_cda', cda)
    mass = params['mass_kg']
    crr = params['crr']
    v_cap = params['desc_speed_cap'] / 3.6

    bands = []
    for label, lo, hi in GRADIENT_BANDS:
        mask = [(lo <= g < hi) for g in grades]
        dist_m = sum(d for d, m in zip(d_dist, mask) if m)
        bands.append({'label': label, 'lo': lo, 'hi': hi, 'dist_m': float(dist_m)})

    total_dist = sum(b['dist_m'] for b in bands)
    if total_dist < 1:
        return None

    # Use segment powers from estimate_time result
    result = estimate_time(params, fit_data)
    if result is None:
        return None
    p_c = result['segments'][0]['power']
    p_f = result['segments'][1]['power']
    d_pow = result['segments'][2]['power']

    for b in bands:
        mid_grade = (b['lo'] + b['hi']) / 2
        mid_grade = max(-0.20, min(0.20, mid_grade))

        if mid_grade < -0.005:
            power = d_pow
        elif mid_grade < 0.005:
            power = p_f
        else:
            power = p_c

        p_wheel = power * eff
        local_cda = climb_cda if mid_grade >= 0.005 else cda
        speed = solve_speed(p_wheel, mid_grade, local_cda, mass, crr, params.get('rho', RHO))
        if mid_grade < -0.005:
            speed = min(speed, v_cap)

        b['pct'] = b['dist_m'] / total_dist * 100
        b['power'] = power
        b['speed_kmh'] = speed * 3.6

    return bands
