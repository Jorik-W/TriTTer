"""Weather retrieval and air-density modeling for the bike estimator.

Two operating modes are supported:

* ``MODE_FORECAST`` ("future prediction") - the route has no per-point time.
  A planned start datetime plus per-location elapsed-time estimates (ETAs)
  are used to project the wall-clock hour at each sample point. Open-Meteo's
  forecast endpoint is queried.
* ``MODE_HISTORY`` ("FIT history") - the file already carries real timestamps
  per point, so those are used directly. Open-Meteo's archive endpoint is
  queried for dates older than ~30 days, otherwise the forecast endpoint.

Design notes:
* Dependencies are kept lean (``requests`` + numpy + stdlib ``datetime``);
  no pandas, to match the rest of this project.
* Sample points are snapped to a ~20 km spatial grid and to the hour, then
  de-duplicated so each grid-cell/hour pair costs at most one API call.
* Air density uses a humidity-corrected (dry + water-vapor partial pressure)
  formulation, with an altitude fallback when barometric pressure is unknown.
"""

import logging
from datetime import datetime, timedelta

import numpy as np

try:
    import requests
    HAS_REQUESTS = True
except ImportError:  # pragma: no cover - exercised only without requests installed
    HAS_REQUESTS = False


OPEN_METEO_URL_FORECAST = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_URL_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"

MODE_FORECAST = "forecast"
MODE_HISTORY = "history"

# ~20 km spatial bucket. 1 degree latitude ~= 111 km, so 20 km ~= 0.18 deg.
LOCAL_RADIUS_KM = 20.0
_GRID_DEG = LOCAL_RADIUS_KM / 111.0

# Physical constants for humidity-corrected air density.
_R_DRY = 287.058    # J/(kg*K) specific gas constant, dry air
_R_VAPOR = 461.495  # J/(kg*K) specific gas constant, water vapor
_KELVIN = 273.15

_DEFAULTS = {
    "temperature": 20.0,   # Celsius
    "wind_speed": 0.0,     # m/s
    "wind_direction": 0.0,  # degrees (meteorological: direction wind comes FROM)
    "pressure": 1013.25,   # hPa
    "humidity": 50.0,      # percent
}

_HOURLY_VARS = (
    "temperature_2m",
    "wind_speed_10m",
    "wind_direction_10m",
    "surface_pressure",
    "relative_humidity_2m",
)


def saturation_vapor_pressure_hpa(temperature_c):
    """Saturation vapor pressure (hPa) via the Magnus/Tetens approximation."""
    t = float(temperature_c)
    return 6.1078 * 10.0 ** (7.5 * t / (t + 237.3))


def pressure_from_altitude_hpa(altitude_m):
    """Estimate barometric pressure (hPa) from altitude via the ISA formula."""
    h = max(0.0, float(altitude_m))
    return 1013.25 * (1.0 - 2.25577e-5 * h) ** 5.25588


def air_density(temperature_c, pressure_hpa=None, humidity_pct=0.0, altitude_m=None):
    """Humidity-corrected air density (kg/m^3).

    rho = pd/(Rd*T) + pv/(Rv*T)

    where ``pd`` and ``pv`` are the partial pressures of dry air and water
    vapor. Humidity lowers density slightly (water vapor is lighter than dry
    air). When ``pressure_hpa`` is unknown, it is estimated from ``altitude_m``;
    if both are missing, sea-level standard pressure is assumed.
    """
    if temperature_c is None:
        temperature_c = _DEFAULTS["temperature"]
    if humidity_pct is None:
        humidity_pct = 0.0

    if pressure_hpa is None:
        if altitude_m is not None:
            pressure_hpa = pressure_from_altitude_hpa(altitude_m)
        else:
            pressure_hpa = _DEFAULTS["pressure"]

    temp_k = float(temperature_c) + _KELVIN
    if temp_k <= 0:
        temp_k = _KELVIN + _DEFAULTS["temperature"]

    humidity_frac = max(0.0, min(1.0, float(humidity_pct) / 100.0))
    p_total_pa = float(pressure_hpa) * 100.0
    p_vapor_pa = humidity_frac * saturation_vapor_pressure_hpa(temperature_c) * 100.0
    p_vapor_pa = min(p_vapor_pa, p_total_pa)  # guard against unphysical inputs
    p_dry_pa = p_total_pa - p_vapor_pa

    return p_dry_pa / (_R_DRY * temp_k) + p_vapor_pa / (_R_VAPOR * temp_k)


class WeatherService:
    """Fetches Open-Meteo weather and de-duplicates calls by 20 km / 1 h grid."""

    def __init__(self, session=None):
        self.logger = logging.getLogger(__name__)
        self._session = session
        if self._session is None and HAS_REQUESTS:
            self._session = requests.Session()

    # -- grid / time helpers -------------------------------------------------

    @staticmethod
    def _grid_key(lat, lon, when):
        """Bucket a sample to (lat_cell, lon_cell, date, hour) for de-duplication."""
        lat_cell = round(float(lat) / _GRID_DEG)
        lon_cell = round(float(lon) / _GRID_DEG)
        return (lat_cell, lon_cell, when.date(), when.hour)

    @staticmethod
    def _grid_center(lat_cell, lon_cell):
        return lat_cell * _GRID_DEG, lon_cell * _GRID_DEG

    @staticmethod
    def _floor_to_hour(when):
        return when.replace(minute=0, second=0, microsecond=0)

    def _endpoint_for_date(self, when):
        one_month_ago = datetime.now().date() - timedelta(days=30)
        if when.date() >= one_month_ago:
            return OPEN_METEO_URL_FORECAST
        return OPEN_METEO_URL_ARCHIVE

    # -- single point fetch --------------------------------------------------

    def get_weather_data(self, latitude, longitude, when, status_callback=None):
        """Return weather dict for one location/time, snapped to the hour.

        Falls back to neutral defaults if ``requests`` is unavailable or the
        API call fails, so the prediction pipeline never hard-crashes on
        connectivity problems.
        """
        if not HAS_REQUESTS or self._session is None:
            if status_callback:
                status_callback("Weather: requests unavailable, using defaults")
            return dict(_DEFAULTS)

        when = self._floor_to_hour(when)
        url = self._endpoint_for_date(when)
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": ",".join(_HOURLY_VARS),
            "wind_speed_unit": "ms",
            "start_date": when.strftime("%Y-%m-%d"),
            "end_date": when.strftime("%Y-%m-%d"),
            "timezone": "auto",
        }

        try:
            response = self._session.get(url, params=params, timeout=10)
            response.raise_for_status()
            hourly = response.json()["hourly"]
            times = [datetime.fromisoformat(t) for t in hourly["time"]]
            target = when.replace(tzinfo=None)
            deltas = np.array(
                [abs((t - target).total_seconds()) for t in times], dtype=float
            )
            idx = int(deltas.argmin())

            return {
                "temperature": self._pick(hourly, "temperature_2m", idx, "temperature"),
                "wind_speed": self._pick(hourly, "wind_speed_10m", idx, "wind_speed"),
                "wind_direction": self._pick(
                    hourly, "wind_direction_10m", idx, "wind_direction"
                ),
                "pressure": self._pick(hourly, "surface_pressure", idx, "pressure"),
                "humidity": self._pick(
                    hourly, "relative_humidity_2m", idx, "humidity"
                ),
            }
        except Exception as exc:  # network/parse failures -> safe defaults
            self.logger.warning("Could not retrieve weather data: %s", exc)
            if status_callback:
                status_callback(f"Weather fetch failed: {exc}")
            return dict(_DEFAULTS)

    @staticmethod
    def _pick(hourly, key, idx, default_key):
        values = hourly.get(key)
        if not values or idx >= len(values) or values[idx] is None:
            return _DEFAULTS[default_key]
        return float(values[idx])

    # -- route prefetch ------------------------------------------------------

    def prefetch_route_weather(self, samples, status_callback=None):
        """Fetch weather for a list of route samples, de-duplicated by grid/hour.

        ``samples`` is a list of dicts with keys ``distance``, ``latitude``,
        ``longitude`` and ``when`` (a ``datetime``). The same list is returned
        with a ``weather`` dict attached to each sample.

        This is mode-agnostic: callers build ``when`` either from real FIT
        timestamps (history mode) or from a planned start time plus ETA
        (forecast mode). See :func:`build_route_samples`.
        """
        grouped = {}
        for s in samples:
            when = self._floor_to_hour(s["when"])
            key = self._grid_key(s["latitude"], s["longitude"], when)
            s["_group_key"] = key
            if key not in grouped:
                lat_c, lon_c = self._grid_center(key[0], key[1])
                grouped[key] = {"lat": lat_c, "lon": lon_c, "when": when}

        if status_callback:
            status_callback(
                f"Weather API request: sample_points={len(samples)}, "
                f"grouped_calls={len(grouped)}"
            )

        results = {}
        for key, q in grouped.items():
            results[key] = self.get_weather_data(
                q["lat"], q["lon"], q["when"], status_callback=status_callback
            )

        for s in samples:
            s["weather"] = results.get(s.pop("_group_key"))
        return samples


def build_route_samples(
    distances,
    latitudes,
    longitudes,
    *,
    mode,
    start_time=None,
    timestamps=None,
    eta_seconds=None,
    sample_distance_m=3000.0,
):
    """Build evenly-spaced weather sample points along a route.

    Parameters
    ----------
    distances, latitudes, longitudes : array-like
        Per-point route geometry (meters / degrees).
    mode : str
        ``MODE_FORECAST`` or ``MODE_HISTORY``.
    start_time : datetime, optional
        Planned start (required for forecast mode).
    timestamps : array-like of datetime, optional
        Real per-point timestamps (required for history mode).
    eta_seconds : array-like, optional
        Predicted elapsed seconds at each point (forecast mode). When omitted,
        all samples share ``start_time`` (single-hour approximation).
    sample_distance_m : float
        Spacing between samples along the route.
    """
    distances = np.asarray(distances, dtype=float)
    latitudes = np.asarray(latitudes, dtype=float)
    longitudes = np.asarray(longitudes, dtype=float)
    n = min(len(distances), len(latitudes), len(longitudes))
    if n == 0:
        return []

    max_distance = float(np.nanmax(distances[:n]))
    step = max(float(sample_distance_m), 1.0)
    if max_distance <= 0:
        sample_points = np.array([0.0], dtype=float)
    else:
        sample_points = np.arange(0.0, max_distance + step, step, dtype=float)
        sample_points[-1] = min(sample_points[-1], max_distance)

    if mode == MODE_HISTORY:
        if timestamps is None:
            raise ValueError("history mode requires per-point timestamps")
        times = list(timestamps)
    elif mode == MODE_FORECAST:
        if start_time is None:
            raise ValueError("forecast mode requires a start_time")
    else:
        raise ValueError(f"unknown weather mode: {mode!r}")

    samples = []
    for sd in sample_points:
        idx = int(np.searchsorted(distances[:n], sd))
        idx = max(0, min(n - 1, idx))

        if mode == MODE_HISTORY:
            when = times[min(idx, len(times) - 1)]
        else:
            elapsed = 0.0
            if eta_seconds is not None and idx < len(eta_seconds):
                elapsed = float(eta_seconds[idx])
            when = start_time + timedelta(seconds=elapsed)

        samples.append(
            {
                "distance": float(distances[idx]),
                "latitude": float(latitudes[idx]),
                "longitude": float(longitudes[idx]),
                "when": when,
            }
        )
    return samples


def compute_route_rho(
    distances,
    latitudes,
    longitudes,
    altitudes=None,
    *,
    mode,
    start_time=None,
    timestamps=None,
    eta_seconds=None,
    service=None,
    status_callback=None,
    sample_distance_m=3000.0,
):
    """Per-segment air density (kg/m^3) aligned to course grades.

    Returns an array of length ``len(distances) - 1`` (one value per segment),
    suitable to pass as ``rho`` into the pacing solver. Each segment takes the
    weather of the nearest fetched sample point and is evaluated at the
    segment's mean altitude. Returns ``None`` when geometry is insufficient,
    so callers can fall back to a constant density.
    """
    samples = fetch_weather_samples(
        distances,
        latitudes,
        longitudes,
        mode=mode,
        start_time=start_time,
        timestamps=timestamps,
        eta_seconds=eta_seconds,
        service=service,
        status_callback=status_callback,
        sample_distance_m=sample_distance_m,
    )
    if samples is None:
        return None
    return densities_from_samples(distances, samples, altitudes=altitudes)


def fetch_weather_samples(
    distances,
    latitudes,
    longitudes,
    *,
    mode,
    start_time=None,
    timestamps=None,
    eta_seconds=None,
    service=None,
    status_callback=None,
    sample_distance_m=3000.0,
):
    """Fetch weather for a route and return the (sparse) sample points.

    The returned list (each item has ``distance`` and ``weather``) is the
    cacheable result of the one network round-trip. Density at any resolution
    can then be derived locally via :func:`densities_from_samples` without
    re-hitting the API. Returns ``None`` when route geometry is insufficient.
    """
    distances = np.asarray(distances, dtype=float)
    latitudes = np.asarray(latitudes, dtype=float) if latitudes is not None else None
    longitudes = np.asarray(longitudes, dtype=float) if longitudes is not None else None
    n_points = len(distances)
    if n_points < 2 or latitudes is None or longitudes is None:
        return None
    if len(latitudes) < n_points or len(longitudes) < n_points:
        return None

    samples = build_route_samples(
        distances,
        latitudes,
        longitudes,
        mode=mode,
        start_time=start_time,
        timestamps=timestamps,
        eta_seconds=eta_seconds,
        sample_distance_m=sample_distance_m,
    )
    if not samples:
        return None

    if service is None:
        service = WeatherService()
    return service.prefetch_route_weather(samples, status_callback=status_callback)


def densities_from_samples(distances, samples, altitudes=None):
    """Per-segment air density from pre-fetched weather samples.

    Cheap, network-free, and resolution-independent: each segment midpoint is
    matched to the nearest sample by along-route distance. ``altitudes`` is
    optional and only used as a pressure fallback when a sample lacks pressure.
    Returns an array of length ``len(distances) - 1`` or ``None``.
    """
    distances = np.asarray(distances, dtype=float)
    n_points = len(distances)
    if n_points < 2 or not samples:
        return None

    if altitudes is not None:
        altitudes = np.asarray(altitudes, dtype=float)
        if len(altitudes) < n_points:
            altitudes = None  # length mismatch -> rely on sample pressure

    sample_dist = np.array([s["distance"] for s in samples], dtype=float)
    sample_weather = [s.get("weather") or dict(_DEFAULTS) for s in samples]

    n_seg = n_points - 1
    rho = np.empty(n_seg, dtype=float)
    for i in range(n_seg):
        mid_dist = 0.5 * (distances[i] + distances[i + 1])
        j = int(np.argmin(np.abs(sample_dist - mid_dist)))
        w = sample_weather[j]
        seg_alt = None
        if altitudes is not None:
            seg_alt = 0.5 * (float(altitudes[i]) + float(altitudes[i + 1]))
        rho[i] = air_density(
            temperature_c=w.get("temperature"),
            pressure_hpa=w.get("pressure"),
            humidity_pct=w.get("humidity"),
            altitude_m=seg_alt,
        )
    return rho


def seed_eta_seconds(distances, grades, *, power_w, cda, mass, crr, eff, rho=None):
    """Rough cumulative elapsed seconds per point for forecast-mode hour bucketing.

    Uses a steady-state speed at a fixed anchor power per segment (no fatigue
    dynamics) so climbs correctly land in later weather hours. Good enough for
    1-hour weather resolution; cheap (one ``solve_speed`` per segment).
    """
    from physics import solve_speed, RHO as _RHO

    distances = np.asarray(distances, dtype=float)
    grades = np.asarray(grades, dtype=float)
    n = len(distances)
    if n < 2:
        return np.zeros(max(n, 1), dtype=float)

    rho_val = _RHO if rho is None else float(rho)
    wheel_power = max(1.0, float(power_w) * float(eff))
    eta = np.zeros(n, dtype=float)
    cumulative = 0.0
    for i in range(n - 1):
        seg_dist = max(0.0, float(distances[i + 1] - distances[i]))
        grade = float(grades[i]) if i < len(grades) else 0.0
        v = solve_speed(wheel_power, grade, cda, mass, crr, rho_val)
        cumulative += seg_dist / max(v, 0.3)
        eta[i + 1] = cumulative
    return eta

