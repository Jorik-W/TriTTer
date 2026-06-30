"""Shared air-density model for TriTTer (Analyze + Plan).

This is the canonical, humidity-corrected air-density formulation, ported from
the former ``bike_estimator`` project. Both the Analyze (CdA) and Plan (pacing)
sides use this so that aerodynamic calculations stay consistent across modes.

    rho = pd / (Rd * T) + pv / (Rv * T)

where ``pd`` and ``pv`` are the partial pressures of dry air and water vapor.
Humidity lowers density slightly (water vapor is lighter than dry air). When
``pressure_hpa`` is unknown it is estimated from ``altitude_m``; if both are
missing, sea-level standard pressure is assumed.
"""

# Physical constants for humidity-corrected air density.
_R_DRY = 287.058    # J/(kg*K) specific gas constant, dry air
_R_VAPOR = 461.495  # J/(kg*K) specific gas constant, water vapor
_KELVIN = 273.15

_DEFAULT_TEMPERATURE_C = 20.0
_DEFAULT_PRESSURE_HPA = 1013.25


def saturation_vapor_pressure_hpa(temperature_c):
    """Saturation vapor pressure (hPa) via the Magnus/Tetens approximation."""
    t = float(temperature_c)
    return 6.1078 * 10.0 ** (7.5 * t / (t + 237.3))


def pressure_from_altitude_hpa(altitude_m):
    """Estimate barometric pressure (hPa) from altitude via the ISA formula."""
    h = max(0.0, float(altitude_m))
    return 1013.25 * (1.0 - 2.25577e-5 * h) ** 5.25588


def air_density(temperature_c, pressure_hpa=None, humidity_pct=0.0, altitude_m=None):
    """Humidity-corrected air density (kg/m^3)."""
    if temperature_c is None:
        temperature_c = _DEFAULT_TEMPERATURE_C
    if humidity_pct is None:
        humidity_pct = 0.0

    if pressure_hpa is None:
        if altitude_m is not None:
            pressure_hpa = pressure_from_altitude_hpa(altitude_m)
        else:
            pressure_hpa = _DEFAULT_PRESSURE_HPA

    temp_k = float(temperature_c) + _KELVIN
    if temp_k <= 0:
        temp_k = _KELVIN + _DEFAULT_TEMPERATURE_C

    humidity_frac = max(0.0, min(1.0, float(humidity_pct) / 100.0))
    p_total_pa = float(pressure_hpa) * 100.0
    p_vapor_pa = humidity_frac * saturation_vapor_pressure_hpa(temperature_c) * 100.0
    p_vapor_pa = min(p_vapor_pa, p_total_pa)  # guard against unphysical inputs
    p_dry_pa = p_total_pa - p_vapor_pa

    return p_dry_pa / (_R_DRY * temp_k) + p_vapor_pa / (_R_VAPOR * temp_k)
