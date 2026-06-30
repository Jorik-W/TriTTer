"""
Advanced durability pacing model.

This model assumes FTP is the threshold (FTP == CP) and applies:
- fatigue-driven FTP decay over accumulated work,
- fatigue-driven reserve-capacity decay,
- gradient x duration x IF segment power caps,
- reserve depletion/recovery simulation.
"""

import heapq

import numpy as np

from physics import G, RHO, solve_speed
from course import GRADIENT_BANDS


CALC_SEGMENT_MAX_M = 25.0
DESCENT_MODE_RACE_SPEED = 'race_speed'

# Baseline air density for weather-impact reporting (ISA sea level, 15C, dry).
RHO_STD = 1.225


def compute_marginal_gain(grade, power, cda, mass, crr, eff, v_cap=None, rho=RHO, wind=0.0):
    """Compute dt/dP: seconds saved per extra watt at this grade/power."""
    dp = 0.5
    p_wheel = power * eff
    p_wheel_hi = (power + dp) * eff

    v = solve_speed(p_wheel, grade, cda, mass, crr, rho, wind_ms=wind)
    v_hi = solve_speed(p_wheel_hi, grade, cda, mass, crr, rho, wind_ms=wind)
    if v_cap is not None:
        v = min(v, v_cap)
        v_hi = min(v_hi, v_cap)

    t = 1.0 / max(v, 0.3)
    t_hi = 1.0 / max(v_hi, 0.3)
    return -(t_hi - t) / dp


def subdivide_course(grades, distances, max_dist_m=CALC_SEGMENT_MAX_M, values=None):
    """Split long course segments into smaller calculation segments.

    When ``values`` is a per-segment array aligned to ``grades`` (e.g. air
    density), it is split in lockstep and returned as a third element so the
    extra array stays index-aligned with the subdivided grades.
    """
    grades = np.array(grades, dtype=float)
    distances = np.array(distances, dtype=float)
    has_values = values is not None and np.ndim(values) > 0
    if has_values:
        values = np.array(values, dtype=float)

    if len(grades) == 0 or len(distances) < 2:
        if has_values:
            return grades, distances, values
        return grades, distances

    new_grades = []
    new_distances = [float(distances[0])]
    new_values = []
    n = min(len(grades), len(distances) - 1)

    for i, (grade, start, end) in enumerate(zip(grades[:n], distances[:n], distances[1:n + 1])):
        dist = max(0.0, float(end - start))
        steps = max(1, int(np.ceil(dist / max_dist_m)))
        step_dist = dist / steps if steps > 0 else 0.0
        seg_value = float(values[i]) if (has_values and i < len(values)) else None
        for _ in range(steps):
            new_grades.append(float(grade))
            new_distances.append(new_distances[-1] + step_dist)
            if has_values:
                new_values.append(seg_value)

    if has_values:
        return np.array(new_grades), np.array(new_distances), np.array(new_values)
    return np.array(new_grades), np.array(new_distances)


def grade_aggression_multiplier(grade):
    """Favor spending IF budget on climbs and backing off on descents."""
    if grade < -0.02:
        return 0.0
    if grade < -0.005:
        return 0.02
    if grade < 0.0:
        return 0.08
    if grade < 0.005:
        return 0.45
    return 1.0


def grade_baseline_power(grade, base_power):
    if grade < -0.05:
        return min(base_power * 0.12, 60.0)
    if grade < -0.02:
        return min(base_power * 0.20, 90.0)
    if grade < -0.005:
        return base_power * 0.35
    if grade < 0.0:
        return base_power * 0.80
    if grade < 0.005:
        return base_power * 0.85
    return base_power


def _gradient_band_index(grade):
    for i, (_label, lo, hi) in enumerate(GRADIENT_BANDS):
        if lo <= grade < hi:
            return i
    return len(GRADIENT_BANDS) - 1


def _gradient_step_bucket(grade, step_pct=0.5):
    """Quantize grade into half-percent buckets for sequential grouping."""
    grade_pct = float(grade) * 100.0
    return round(grade_pct / step_pct) * step_pct


def _power_step_bucket(power_w, step_w=20.0):
    """Quantize power to configurable buckets for stable grouping."""
    return round(float(power_w) / max(1.0, float(step_w))) * max(1.0, float(step_w))


def short_segment_multiplier(duration_s):
    if duration_s >= 60.0:
        return 1.0
    return 1.0 + 0.75 * (60.0 - duration_s) / 60.0


def _speed_with_inertia(prev_speed, steady_speed, power, grade, cda, mass, crr, eff, dist_m, v_cap, rho=RHO, wind=0.0):
    """Blend toward steady-state speed with simple acceleration limits."""
    v_ss = min(max(float(steady_speed), 0.3), v_cap)
    if prev_speed is None or dist_m <= 0.1:
        return v_ss

    v_prev = min(max(float(prev_speed), 0.3), v_cap)
    if abs(v_ss - v_prev) < 1e-6:
        return v_ss

    v_air = v_prev + float(wind)
    aero_force = 0.5 * rho * cda * v_air * abs(v_air)
    slope_force = (crr + grade) * mass * G
    drive_force = max(0.0, power * eff) / max(v_prev, 0.3)
    net_force = drive_force - (aero_force + slope_force)

    if v_ss > v_prev:
        accel = max(0.05, net_force / max(mass, 1.0))
    else:
        decel_force = (aero_force + slope_force) - drive_force
        accel = max(0.10, abs(decel_force) / max(mass, 1.0))

    dv = abs(v_ss - v_prev)
    t_to_ss = dv / max(accel, 0.05)
    t_seg = dist_m / max(v_ss, 0.3)
    blend = 1.0 - np.exp(-t_seg / max(t_to_ss, 0.1))

    v = v_prev + (v_ss - v_prev) * blend
    return min(max(v, 0.3), v_cap)


def _solve_speed_with_kinetic_energy(v_in, power, grade, distance_m, cda, mass, crr, eff, v_cap, rho=RHO, wind=0.0):
    """Integrate speed over segment distance using force balance and rider power."""
    v = max(0.3, float(v_in))
    power = max(0.0, float(power))
    distance_m = max(0.1, float(distance_m))
    mass = max(1.0, float(mass))
    cda = max(1e-6, float(cda))
    crr = max(0.0, float(crr))
    eff = max(0.1, float(eff))
    grade = float(grade)
    v_cap = max(0.3, float(v_cap))
    rho = max(0.1, float(rho))
    wind = float(wind)

    # Substep integration stabilizes acceleration/deceleration on short segments.
    steps = max(1, int(np.ceil(distance_m / 5.0)))
    ds = distance_m / steps
    wheel_power = power * eff
    rolling_slope_force = (crr + grade) * mass * G

    for _ in range(steps):
        v_eff = max(v, 0.3)
        v_air = v_eff + wind
        aero_force = 0.5 * rho * cda * v_air * abs(v_air)
        drive_force = wheel_power / v_eff
        net_force = drive_force - (aero_force + rolling_slope_force)
        accel = net_force / mass

        # Distance-domain kinematics: v_out^2 = v_in^2 + 2*a*ds
        v_sq = v * v + 2.0 * accel * ds
        if v_sq <= 0.09:
            v = 0.3
        else:
            v = np.sqrt(v_sq)
        v = min(max(float(v), 0.3), v_cap)

    return v


def _duration_factor(duration_s):
    if duration_s < 30.0:
        return 1.15
    if duration_s < 60.0:
        return 1.10
    if duration_s < 180.0:
        return 1.00
    if duration_s < 480.0:
        return 0.95
    if duration_s < 1200.0:
        return 0.90
    if duration_s < 2400.0:
        return 0.85
    return 0.80


def _gradient_factor(grade):
    grade_pct = grade * 100.0
    if grade_pct < 2.0:
        return 1.05
    if grade_pct < 4.0:
        return 1.10
    if grade_pct < 6.0:
        return 1.20
    if grade_pct < 8.0:
        return 1.30
    if grade_pct < 10.0:
        return 1.45
    if grade_pct < 12.0:
        return 1.60
    if grade_pct < 15.0:
        return 1.80
    return 2.20


def _if_adjustment(target_if):
    return max(0.80, min(1.05, 1.0 - (target_if - 0.75) / 2.0))


def _fatigue_drop_from_work(accum_kj):
    """Piecewise linear FTP drop from accumulated work (kJ)."""
    points_kj = np.array([0.0, 1500.0, 2500.0, 3500.0, 4500.0, 7000.0], dtype=float)
    points_drop = np.array([0.0, 0.03, 0.06, 0.10, 0.15, 0.20], dtype=float)
    return float(np.interp(max(0.0, accum_kj), points_kj, points_drop))


def _reserve_capacity_scale(accum_kj, mass_kg, decay_k):
    """Exponential reserve decay using normalized kJ/kg fatigue signal."""
    mass_kg = max(1.0, float(mass_kg))
    fatigue_score = max(0.0, accum_kj / mass_kg) / 60.0
    return float(np.exp(-max(0.0, decay_k) * fatigue_score))


def _segment_power_cap(np_anchor, grade, duration_s, target_if, fatigue_drop):
    g = _gradient_factor(grade)
    d = _duration_factor(duration_s)
    if_adj = _if_adjustment(target_if)
    fatigue_adj = max(0.75, 1.0 - fatigue_drop)
    return np_anchor * g * d * if_adj * fatigue_adj


def _terminal_velocity_no_pedal(grade, mass, cda, crr, rho=RHO, wind=0.0):
    """Estimate terminal velocity on descents from force balance (no pedaling)."""
    if grade >= 0.0:
        return 0.3

    downhill = abs(float(grade))
    gravity_drive = mass * G * downhill
    rolling = crr * mass * G
    aero = 0.5 * rho * max(cda, 1e-6)
    net = gravity_drive - rolling
    if net <= 0.0:
        return 0.3
    v_air = float(np.sqrt(net / max(aero, 1e-9)))
    # v_air = v_ground + headwind; convert back to ground speed.
    return max(0.3, v_air - float(wind))


def _descent_power_bounds_race_speed(threshold_power, grade, reserve_ratio):
    """Speed-priority downhill bounds with mild-gradient pedaling floors."""
    g_pct = abs(float(grade)) * 100.0

    if g_pct < 1.0:
        if reserve_ratio > 0.8:
            lo, hi = 0.60, 0.80
        elif reserve_ratio > 0.5:
            lo, hi = 0.40, 0.60
        else:
            lo, hi = 0.20, 0.40
    elif g_pct < 2.0:
        if reserve_ratio > 0.8:
            lo, hi = 0.60, 0.80
        elif reserve_ratio > 0.5:
            lo, hi = 0.40, 0.60
        else:
            lo, hi = 0.15, 0.35
    elif g_pct < 4.0:
        if reserve_ratio > 0.8:
            lo, hi = 0.40, 0.60
        elif reserve_ratio > 0.5:
            lo, hi = 0.20, 0.40
        else:
            lo, hi = 0.00, 0.20
    elif g_pct < 6.0:
        if reserve_ratio > 0.8:
            lo, hi = 0.20, 0.40
        elif reserve_ratio > 0.5:
            lo, hi = 0.00, 0.20
        else:
            lo, hi = 0.00, 0.00
    elif g_pct < 8.0:
        if reserve_ratio > 0.8:
            lo, hi = 0.00, 0.20
        else:
            lo, hi = 0.00, 0.00
    elif g_pct < 10.0:
        lo, hi = 0.00, 0.00
    else:
        lo, hi = 0.00, 0.00

    return threshold_power * lo, threshold_power * hi


def _descent_power_bounds(threshold_power, grade, reserve_ratio, descent_mode):
    return _descent_power_bounds_race_speed(threshold_power, grade, reserve_ratio)


def _descent_target_power(grade, reserve_ratio, est_duration_s, next_grade, low_w, high_w, descent_mode):
    """Select descent power with recovery-first heuristics."""
    if high_w <= 0.0:
        return 0.0

    # Deep depletion: maximize reserve reconstitution.
    if reserve_ratio < 0.50:
        if grade > -0.02:
            return low_w
        return 0.0

    # If a harder segment is next, prioritize recovery over downhill pedaling.
    if next_grade > max(0.01, grade + 0.02):
        return low_w

    # Duration-aware descent strategy.
    if est_duration_s < 30.0:
        return low_w + 0.75 * (high_w - low_w)
    if est_duration_s < 90.0:
        if reserve_ratio < 0.70:
            return low_w
        return low_w + 0.40 * (high_w - low_w)
    if est_duration_s < 180.0:
        if reserve_ratio < 0.85:
            return low_w
        return low_w + 0.20 * (high_w - low_w)
    if est_duration_s < 480.0:
        if reserve_ratio < 0.90:
            return low_w
        return low_w + 0.10 * (high_w - low_w)

    # Very long descents: keep minimal metabolic load.
    return low_w


def _apply_power_variability_guard(power, grade, threshold_now, target_if):
    """Limit power dispersion so NP and Avg Power stay closer while preserving terrain response."""
    anchor = max(1.0, float(threshold_now) * max(0.1, float(target_if)))
    power = float(power)
    grade = float(grade)

    if grade < -0.03:
        return min(power, 0.60 * anchor)
    if grade < -0.01:
        return min(power, 0.75 * anchor)

    if grade < 0.01:
        lo, hi = 0.85 * anchor, 1.10 * anchor
    elif grade < 0.04:
        lo, hi = 0.90 * anchor, 1.18 * anchor
    elif grade < 0.08:
        lo, hi = 0.92 * anchor, 1.25 * anchor
    else:
        lo, hi = 0.95 * anchor, 1.35 * anchor

    return min(max(power, lo), hi)


def _effective_cda_for_segment(base_cda, climb_cda, grade, reference_speed):
    """Use climbing-position CdA only on slow climbs."""
    if climb_cda is None:
        return base_cda

    if float(grade) <= 0.02:
        return base_cda

    if reference_speed is None:
        return base_cda

    if float(reference_speed) < (24.0 / 3.6):
        return float(climb_cda)

    return base_cda


def pacing_curve(
    grade,
    threshold_power,
    base_power,
    k,
    cda,
    mass,
    crr,
    eff,
    v_cap,
    dist_m,
    np_anchor,
    target_if,
    fatigue_drop,
    rho=RHO,
    wind=0.0,
):
    """Compute terrain-aware target power for one segment."""
    speed_threshold = solve_speed(threshold_power * eff, grade, cda, mass, crr, rho, wind_ms=wind)
    speed_threshold = min(speed_threshold, v_cap)
    duration_at_threshold = dist_m / max(speed_threshold, 0.3)

    cheapness = 1.0 / max(duration_at_threshold, 30.0)
    local_base_power = grade_baseline_power(grade, base_power)
    mg = compute_marginal_gain(grade, max(local_base_power, 1.0), cda, mass, crr, eff, v_cap, rho, wind=wind)

    if mg <= 0:
        raw_power = local_base_power
    else:
        raw_power = (
            local_base_power
            + k
            * mg
            * cheapness
            * grade_aggression_multiplier(grade)
            * short_segment_multiplier(duration_at_threshold)
        )

    if grade < 0.0:
        # Uphill cap logic does not apply on descents.
        cap_power = float('inf')
    else:
        cap_power = _segment_power_cap(
            np_anchor=np_anchor,
            grade=grade,
            duration_s=duration_at_threshold,
            target_if=target_if,
            fatigue_drop=fatigue_drop,
        )
    return max(0.0, min(raw_power, cap_power)), cap_power


def simulate_reserve_balance(
    grades,
    distances,
    ftp,
    target_if,
    base_power,
    k,
    reserve_j,
    cda,
    mass,
    crr,
    eff,
    v_cap,
    max_power_w,
    reserve_decay_k,
    descent_mode=DESCENT_MODE_RACE_SPEED,
    climb_cda=None,
    rho=RHO,
    wind=0.0,
):
    """Sequential reserve-balance simulation with fatigue-updated threshold and reserve."""
    n = len(grades)
    d_dist = np.diff(distances)

    seg_power = np.zeros(n)
    seg_cap = np.zeros(n)
    seg_speed = np.zeros(n)
    seg_time = np.zeros(n)
    seg_reserve = np.zeros(n)
    seg_threshold = np.zeros(n)
    seg_fatigue_drop = np.zeros(n)
    seg_accum_kj = np.zeros(n)
    seg_weather_w = np.zeros(n)
    seg_weather_kmh = np.zeros(n)
    seg_weather_time_s = np.zeros(n)

    reserve_bal = reserve_j
    accum_work_j = 0.0
    max_power_w = max(0.0, max_power_w)
    prev_speed = None
    np_anchor = ftp * target_if
    rho_is_array = np.ndim(rho) > 0
    wind_is_array = np.ndim(wind) > 0

    for i in range(n):
        dist = d_dist[i] if i < len(d_dist) else 0.0
        if dist < 0.1:
            seg_power[i] = min(base_power, max_power_w)
            seg_speed[i] = 0.3
            seg_time[i] = 0.0
            seg_reserve[i] = reserve_bal
            seg_threshold[i] = ftp
            continue

        grade = grades[i]
        rho_i = float(rho[i]) if (rho_is_array and i < len(rho)) else (RHO if rho_is_array else rho)
        wind_i = float(wind[i]) if (wind_is_array and i < len(wind)) else (0.0 if wind_is_array else float(wind))
        accum_kj = accum_work_j / 1000.0
        fatigue_drop = _fatigue_drop_from_work(accum_kj)
        threshold_now = ftp * (1.0 - fatigue_drop)

        reserve_scale = _reserve_capacity_scale(accum_kj, mass, reserve_decay_k)
        reserve_cap_now = max(1000.0, reserve_j * reserve_scale)
        reserve_bal = min(reserve_bal, reserve_cap_now)

        if prev_speed is None:
            ref_speed = solve_speed(base_power * eff, grade, cda, mass, crr, rho_i, wind_ms=wind_i)
        else:
            ref_speed = prev_speed
        cda_now = _effective_cda_for_segment(cda, climb_cda, grade, ref_speed)

        power, cap_power = pacing_curve(
            grade=grade,
            threshold_power=threshold_now,
            base_power=base_power,
            k=k,
            cda=cda_now,
            mass=mass,
            crr=crr,
            eff=eff,
            v_cap=v_cap,
            dist_m=dist,
            np_anchor=np_anchor,
            target_if=target_if,
            fatigue_drop=fatigue_drop,
            rho=rho_i,
            wind=wind_i,
        )

        if grade < 0.0:
            next_grade = float(grades[i + 1]) if (i + 1) < n else 0.0
            coast_v = min(_terminal_velocity_no_pedal(grade, mass, cda_now, crr, rho_i, wind=wind_i), v_cap)
            est_duration_s = dist / max(coast_v, 0.3)
            reserve_ratio = reserve_bal / max(reserve_cap_now, 1.0)
            
            # **KEY DESCENT RULE**: If v_in >= v_terminal, rider already decelerating from drag.
            # Adding power returns marginal benefit sharp diminish → assign 0 W (coast).
            strict_terminal_coast = (
                prev_speed is not None
                and prev_speed >= coast_v + 0.5
            )
            race_speed_terminal_coast = (
                grade < -0.025
                and prev_speed is not None
                and prev_speed >= min(v_cap * 0.92, coast_v + 0.5)
            )

            if race_speed_terminal_coast:
                power = 0.0
                cap_power = 0.0
            else:
                # Below terminal velocity: use reserve-state floor table for power selection.
                low_w, high_w = _descent_power_bounds(
                    threshold_now, grade, reserve_ratio, descent_mode
                )
                power = _descent_target_power(
                    grade=grade,
                    reserve_ratio=reserve_ratio,
                    est_duration_s=est_duration_s,
                    next_grade=next_grade,
                    low_w=low_w,
                    high_w=high_w,
                    descent_mode=descent_mode,
                )
                cap_power = high_w
        elif (
            grade >= 0.01  # Real climb
            and i > 0
            and prev_speed is not None
            and prev_speed > 13.0  # High entry speed (>13 m/s ≈ 47 km/h)
            and float(grades[i - 1]) < -0.02  # Previous segment is steep descent (< -2%)
        ):
            # Kinetic taper: high-speed descent-to-climb transition.
            # Reduce power early in climb as kinetic energy converts to elevation.
            v_in_squared = prev_speed ** 2
            d_taper = v_in_squared / (2.0 * (G * abs(grade) + crr * G))
            if d_taper > 20.0:  # Only apply if taper distance > 20m
                taper_progress = min(1.0, dist / (d_taper / 3.0))
                power *= max(0.75, 1.0 - 0.25 * (1.0 - taper_progress))

        power = min(power, max_power_w)
        power = _apply_power_variability_guard(power, grade, threshold_now, target_if)
        power = min(power, max_power_w)

        v_in_seg = prev_speed

        # Solve for exit speed using full kinetic energy balance equation.
        if v_in_seg is not None and dist > 0.1:
            speed = _solve_speed_with_kinetic_energy(
            v_in=v_in_seg,
                power=power,
                grade=grade,
                distance_m=dist,
                cda=cda_now,
                mass=mass,
                crr=crr,
                eff=eff,
                v_cap=v_cap,
                rho=rho_i,
                wind=wind_i,
            )
        else:
            # First segment or zero distance: use steady-state + blending
            speed_ss = solve_speed(power * eff, grade, cda_now, mass, crr, rho_i, wind_ms=wind_i)
            speed = _speed_with_inertia(
                v_in_seg, speed_ss, power, grade, cda_now, mass, crr, eff, dist, v_cap, rho_i, wind=wind_i
            )
        speed = min(max(speed, 0.3), v_cap)

        if v_in_seg is not None and dist > 0.1:
            speed_for_time = 0.5 * (max(v_in_seg, 0.3) + speed)
        else:
            speed_for_time = speed

        prev_speed = speed
        dt = dist / max(speed_for_time, 0.3)

        if power > threshold_now:
            reserve_bal -= (power - threshold_now) * dt
        elif power < threshold_now:
            recovery_rate = ((threshold_now - power) / max(reserve_cap_now, 1.0)) * (1.0 - 0.6 * fatigue_drop)
            recovery_rate = max(0.0, recovery_rate)
            reserve_bal += (reserve_cap_now - reserve_bal) * (1.0 - np.exp(-recovery_rate * dt))

        reserve_bal = max(0.0, min(reserve_cap_now, reserve_bal))
        accum_work_j += power * dt

        seg_power[i] = power
        seg_cap[i] = cap_power
        seg_speed[i] = speed
        seg_time[i] = dt
        seg_reserve[i] = reserve_bal
        seg_threshold[i] = threshold_now
        seg_fatigue_drop[i] = fatigue_drop
        seg_accum_kj[i] = accum_work_j / 1000.0

        # Weather impact vs standard still air (1.225, no wind): km/h and watts
        # gained/lost at planned power. Dense air or headwind -> +W and -km/h;
        # thin air or tailwind -> -W and +km/h. Includes both density and wind.
        v_real = min(solve_speed(power * eff, grade, cda_now, mass, crr, rho_i, wind_ms=wind_i), v_cap)
        v_std = min(solve_speed(power * eff, grade, cda_now, mass, crr, RHO_STD), v_cap)
        v_air_real = speed + wind_i
        seg_weather_w[i] = 0.5 * cda_now * speed * (
            rho_i * v_air_real * abs(v_air_real) - RHO_STD * speed * speed
        )
        seg_weather_kmh[i] = (v_real - v_std) * 3.6
        if v_real > 1e-6 and v_std > 1e-6:
            seg_weather_time_s[i] = dist / v_real - dist / v_std

    total_time = np.sum(seg_time)
    mask = seg_time > 0
    if np.sum(mask) == 0:
        return None

    avg_power = np.sum(seg_power[mask] * seg_time[mask]) / total_time
    np_power = (np.sum(seg_power[mask] ** 4 * seg_time[mask]) / total_time) ** 0.25
    total_dist = distances[-1] - distances[0] if len(distances) > 1 else 0
    avg_speed = (total_dist / total_time) * 3.6 if total_time > 0 else 0

    return {
        'total_time_s': total_time,
        'time_h': total_time / 3600,
        'avg_power': avg_power,
        'np_power': np_power,
        'avg_speed': avg_speed,
        'wkg': avg_power / mass,
        'min_reserve': float(np.min(seg_reserve[mask])),
        'final_reserve': float(seg_reserve[mask][-1]) if np.any(mask) else reserve_j,
        'final_threshold': float(seg_threshold[mask][-1]) if np.any(mask) else ftp,
        'final_fatigue_drop': float(seg_fatigue_drop[mask][-1]) if np.any(mask) else 0.0,
        'total_work_kj': float(accum_work_j / 1000.0),
        'seg_power': seg_power,
        'seg_cap': seg_cap,
        'seg_speed': seg_speed,
        'seg_time': seg_time,
        'seg_reserve': seg_reserve,
        'seg_threshold': seg_threshold,
        'seg_fatigue_drop': seg_fatigue_drop,
        'seg_accum_kj': seg_accum_kj,
        'seg_weather_w': seg_weather_w,
        'seg_weather_kmh': seg_weather_kmh,
        'seg_weather_time_s': seg_weather_time_s,
        'weather_time_s': float(np.sum(seg_weather_time_s)),
    }


def group_power_sections(sim_result, grades, distances, tolerance_w=None, target_segments=None):
    """Group consecutive segments by gradient and power continuity (course order)."""
    d_dist = np.diff(distances)
    n = min(len(grades), len(d_dist), len(sim_result['seg_power']))
    power_tolerance_w = 22.0 if tolerance_w is None else max(0.0, float(tolerance_w))
    max_primary_group_dist_m = 3500.0
    seg_reserve = sim_result.get('seg_reserve')
    if seg_reserve is None:
        seg_reserve = sim_result.get('seg_wbal', np.zeros(n))
    seg_reserve = np.array(seg_reserve, dtype=float)
    if len(seg_reserve) < n:
        pad = np.zeros(max(0, n - len(seg_reserve)))
        seg_reserve = np.concatenate([seg_reserve, pad]) if len(seg_reserve) else np.zeros(n)

    seg_cap = sim_result.get('seg_cap')
    if seg_cap is None:
        seg_cap = sim_result['seg_power']
    seg_cap = np.array(seg_cap, dtype=float)
    if len(seg_cap) < n:
        seg_cap = np.resize(seg_cap, n)

    seg_threshold = sim_result.get('seg_threshold')
    if seg_threshold is None:
        seg_threshold = np.full(n, sim_result.get('ftp', 0.0), dtype=float)
    seg_threshold = np.array(seg_threshold, dtype=float)
    if len(seg_threshold) < n:
        seg_threshold = np.resize(seg_threshold, n)

    seg_fatigue = sim_result.get('seg_fatigue_drop')
    if seg_fatigue is None:
        seg_fatigue = np.zeros(n, dtype=float)
    seg_fatigue = np.array(seg_fatigue, dtype=float)
    if len(seg_fatigue) < n:
        seg_fatigue = np.resize(seg_fatigue, n)

    seg_weather_w = np.array(sim_result.get('seg_weather_w', np.zeros(n)), dtype=float)
    if len(seg_weather_w) < n:
        seg_weather_w = np.resize(seg_weather_w, n) if len(seg_weather_w) else np.zeros(n)
    seg_weather_kmh = np.array(sim_result.get('seg_weather_kmh', np.zeros(n)), dtype=float)
    if len(seg_weather_kmh) < n:
        seg_weather_kmh = np.resize(seg_weather_kmh, n) if len(seg_weather_kmh) else np.zeros(n)

    sections = []
    seg_target_power = np.array(sim_result['seg_power'], dtype=float)
    min_group_dist_m = 1.0

    start = 0
    while start < n:
        # Ignore degenerate geometry segments created by repeated distance samples.
        while start < n and float(d_dist[start]) <= min_group_dist_m:
            start += 1
        if start >= n:
            break

        anchor_step = _gradient_step_bucket(float(grades[start]), step_pct=0.5)
        anchor_power = float(sim_result['seg_power'][start])
        anchor_power_bucket = _power_step_bucket(float(sim_result['seg_power'][start]), step_w=20.0)
        end = start + 1
        group_dist_m = float(d_dist[start]) if start < len(d_dist) else 0.0
        while end < n:
            if float(d_dist[end]) <= min_group_dist_m:
                end += 1
                continue
            next_step = _gradient_step_bucket(float(grades[end]), step_pct=0.5)
            next_power = float(sim_result['seg_power'][end])
            prev_power = float(sim_result['seg_power'][end - 1])
            next_power_bucket = _power_step_bucket(next_power, step_w=20.0)
            if next_step != anchor_step:
                break
            # Prevent long-range drift sections from hiding local variability.
            if abs(next_power - anchor_power) > (power_tolerance_w * 1.25):
                break
            if next_power_bucket != anchor_power_bucket and abs(next_power - prev_power) > power_tolerance_w:
                break
            next_dist = float(d_dist[end]) if end < len(d_dist) else 0.0
            if group_dist_m + next_dist > max_primary_group_dist_m:
                break
            group_dist_m += next_dist
            end += 1

        sl = slice(start, end)
        dist_m = float(np.sum(d_dist[sl]))
        time_s = float(np.sum(sim_result['seg_time'][sl]))
        if time_s > 0:
            avg_power = float(np.sum(sim_result['seg_power'][sl] * sim_result['seg_time'][sl]) / time_s)
            speed_kmh = (dist_m / time_s) * 3.6
            avg_cap = float(np.sum(seg_cap[sl] * sim_result['seg_time'][sl]) / time_s)
            avg_threshold = float(np.sum(seg_threshold[sl] * sim_result['seg_time'][sl]) / time_s)
        else:
            avg_power = float(np.mean(sim_result['seg_power'][sl]))
            speed_kmh = 0.0
            avg_cap = float(np.mean(seg_cap[sl]))
            avg_threshold = float(np.mean(seg_threshold[sl]))

        avg_grade = float(np.sum(np.array(grades[:n])[sl] * d_dist[sl]) / max(dist_m, 1.0))
        min_reserve_default = sim_result.get('min_reserve', sim_result.get('min_wbal', 0.0))
        min_reserve = float(np.min(seg_reserve[sl])) if end > start else min_reserve_default
        max_fatigue_drop = float(np.max(seg_fatigue[sl])) if end > start else 0.0

        if time_s > 0:
            weather_w = float(np.sum(seg_weather_w[sl] * sim_result['seg_time'][sl]) / time_s)
            weather_kmh = float(np.sum(seg_weather_kmh[sl] * sim_result['seg_time'][sl]) / time_s)
        else:
            weather_w = float(np.mean(seg_weather_w[sl])) if end > start else 0.0
            weather_kmh = float(np.mean(seg_weather_kmh[sl])) if end > start else 0.0

        seg_target_power[sl] = avg_power
        sections.append({
            'start_km': float(distances[start] / 1000),
            'end_km': float(distances[end] / 1000),
            'dist_km': dist_m / 1000,
            'avg_grade': avg_grade,
            'power': avg_power,
            'cap_power': avg_cap,
            'threshold_power': avg_threshold,
            'speed_kmh': speed_kmh,
            'time_s': time_s,
            'min_reserve': min_reserve,
            'fatigue_drop_pct': max_fatigue_drop * 100.0,
            'weather_w': weather_w,
            'weather_kmh': weather_kmh,
        })
        start = end

    def _merge_two_sections(a, b):
        wa = max(0.0, float(a['time_s']))
        wb = max(0.0, float(b['time_s']))
        if wa + wb <= 0.0:
            wa = max(0.0, float(a['dist_km']))
            wb = max(0.0, float(b['dist_km']))
        wsum = max(1e-9, wa + wb)

        return {
            'start_km': float(a['start_km']),
            'end_km': float(b['end_km']),
            'dist_km': float(a['dist_km'] + b['dist_km']),
            'avg_grade': float((a['avg_grade'] * wa + b['avg_grade'] * wb) / wsum),
            'power': float((a['power'] * wa + b['power'] * wb) / wsum),
            'cap_power': float((a['cap_power'] * wa + b['cap_power'] * wb) / wsum),
            'threshold_power': float((a['threshold_power'] * wa + b['threshold_power'] * wb) / wsum),
            'speed_kmh': float((a['speed_kmh'] * wa + b['speed_kmh'] * wb) / wsum),
            'time_s': float(a['time_s'] + b['time_s']),
            'min_reserve': float(min(a['min_reserve'], b['min_reserve'])),
            'fatigue_drop_pct': float(max(a['fatigue_drop_pct'], b['fatigue_drop_pct'])),
            'weather_w': float((a.get('weather_w', 0.0) * wa + b.get('weather_w', 0.0) * wb) / wsum),
            'weather_kmh': float((a.get('weather_kmh', 0.0) * wa + b.get('weather_kmh', 0.0) * wb) / wsum),
        }

    def _merge_display_sections(raw_sections, power_tol_w):
        if not raw_sections:
            return raw_sections

        min_dist_km = 0.05
        min_time_s = 12.0
        max_merge_dist_km = 4.0
        max_merge_time_s = 900.0
        grade_tol = 0.0035
        similarity_power_tol = max(16.0, min(28.0, float(power_tol_w)))

        merged = [dict(raw_sections[0])]
        for sec in raw_sections[1:]:
            prev = merged[-1]
            prev_small = prev['dist_km'] < min_dist_km or prev['time_s'] < min_time_s
            curr_small = sec['dist_km'] < min_dist_km or sec['time_s'] < min_time_s
            compatible = (
                abs(float(sec['avg_grade']) - float(prev['avg_grade'])) <= grade_tol
                and abs(float(sec['power']) - float(prev['power'])) <= similarity_power_tol
            )
            merged_dist = float(prev['dist_km']) + float(sec['dist_km'])
            merged_time = float(prev['time_s']) + float(sec['time_s'])
            exceeds_caps = merged_dist > max_merge_dist_km or merged_time > max_merge_time_s

            # Merge only to absorb tiny slivers, not to smooth long route blocks.
            should_merge = compatible and (prev_small or curr_small) and (not exceeds_caps)
            if not should_merge:
                merged.append(dict(sec))
                continue

            merged[-1] = _merge_two_sections(prev, sec)
        return merged

    def _section_similarity_score(a, b):
        grade_term = abs(float(a['avg_grade']) - float(b['avg_grade'])) * 100.0
        power_term = abs(float(a['power']) - float(b['power'])) / 25.0
        cap_term = abs(float(a['cap_power']) - float(b['cap_power'])) / 40.0
        return grade_term + power_term + cap_term

    def _almost_same(a, b):
        return (
            abs(float(a['avg_grade']) - float(b['avg_grade'])) <= 0.002
            and abs(float(a['power']) - float(b['power'])) <= 10.0
            and abs(float(a['cap_power']) - float(b['cap_power'])) <= 15.0
            and abs(float(a['threshold_power']) - float(b['threshold_power'])) <= 15.0
        )

    def _normalize_sections(raw_sections):
        if not raw_sections:
            return raw_sections

        cleaned = []
        for sec in raw_sections:
            dist_km = float(sec.get('dist_km', 0.0))
            time_s = float(sec.get('time_s', 0.0))
            speed_kmh = float(sec.get('speed_kmh', 0.0))
            cap_power = float(sec.get('cap_power', 0.0))

            # Drop display artifacts: zero-length rows and impossible zero-speed/zero-cap placeholders.
            if dist_km <= 0.001 or time_s <= 0.5:
                continue
            if speed_kmh <= 0.1 and cap_power <= 0.1:
                continue
            cleaned.append(dict(sec))

        if not cleaned:
            return []

        deduped = [cleaned[0]]
        for sec in cleaned[1:]:
            prev = deduped[-1]
            if _almost_same(prev, sec):
                deduped[-1] = _merge_two_sections(prev, sec)
            else:
                deduped.append(sec)

        # Force-absorb tiny display rows to avoid 0.0k / 0h00 noise in the table.
        min_display_dist_km = 0.08
        min_display_time_s = 15.0
        i = 0
        while i < len(deduped):
            curr = deduped[i]
            tiny = float(curr['dist_km']) < min_display_dist_km or float(curr['time_s']) < min_display_time_s
            if not tiny:
                i += 1
                continue

            if len(deduped) == 1:
                break

            if i == 0:
                deduped[1] = _merge_two_sections(curr, deduped[1])
                del deduped[0]
                continue

            if i == len(deduped) - 1:
                deduped[i - 1] = _merge_two_sections(deduped[i - 1], curr)
                del deduped[i]
                i -= 1
                continue

            prev = deduped[i - 1]
            nxt = deduped[i + 1]
            if _section_similarity_score(curr, prev) <= _section_similarity_score(curr, nxt):
                deduped[i - 1] = _merge_two_sections(prev, curr)
                del deduped[i]
                i -= 1
            else:
                deduped[i + 1] = _merge_two_sections(curr, nxt)
                del deduped[i]

        # Final adjacency collapse after tiny-row absorption.
        final_sections = [deduped[0]] if deduped else []
        for sec in deduped[1:]:
            prev = final_sections[-1]
            if _almost_same(prev, sec):
                final_sections[-1] = _merge_two_sections(prev, sec)
            else:
                final_sections.append(sec)

        return final_sections

    def _bottom_up_merge(raw_sections, target_n):
        """Heap-based bottom-up merge: repeatedly merge the adjacent pair with the
        smallest power difference until len == target_n."""
        n = len(raw_sections)
        if target_n >= n or n <= 1:
            return list(raw_sections)
        target_n = max(1, int(target_n))

        nodes = {}
        for i, sec in enumerate(raw_sections):
            nodes[i] = dict(sec)
            nodes[i].update({'_id': i, '_prev': i - 1 if i > 0 else None,
                              '_next': i + 1 if i < n - 1 else None, '_alive': True})

        heap = []

        def _push(lid, rid):
            if lid is None or rid is None:
                return
            cost = abs(nodes[lid]['power'] - nodes[rid]['power'])
            heapq.heappush(heap, (cost, lid, rid))

        for i in range(n - 1):
            _push(i, i + 1)

        alive = n
        while alive > target_n and heap:
            cost, lid, rid = heapq.heappop(heap)
            L, R = nodes.get(lid), nodes.get(rid)
            if not L or not R or not L['_alive'] or not R['_alive']:
                continue
            if L['_next'] != rid or R['_prev'] != lid:
                continue  # stale
            merged = _merge_two_sections(L, R)
            merged.update({'_id': lid, '_prev': L['_prev'],
                           '_next': R['_next'], '_alive': True})
            nodes[lid] = merged
            if R['_next'] is not None:
                nodes[R['_next']]['_prev'] = lid
            nodes[rid]['_alive'] = False
            alive -= 1
            _push(merged['_prev'], lid)
            _push(lid, merged['_next'])

        head = next((s for s in nodes.values() if s['_alive'] and s['_prev'] is None), None)
        result, cur = [], head
        while cur is not None:
            result.append({k: v for k, v in cur.items() if not k.startswith('_')})
            cur = nodes[cur['_next']] if cur['_next'] is not None else None
        return result

    sections = _merge_display_sections(sections, power_tolerance_w)
    sections = _normalize_sections(sections)
    natural_count = len(sections)
    if target_segments is not None and 1 <= int(target_segments) < natural_count:
        sections = _bottom_up_merge(sections, int(target_segments))
    return sections, seg_target_power, natural_count


def _finish_result(result, grades, distances, ftp, target_if, if_margin, min_reserve_warn_j, target_segments=None):
    if result is None:
        return None

    achieved_if = result['np_power'] / ftp if ftp > 0 else 0.0
    low = max(0.0, target_if - if_margin)
    high = target_if + if_margin
    if achieved_if < low:
        if_status = 'low'
    elif achieved_if > high:
        if_status = 'high'
    else:
        if_status = 'ok'

    sections, seg_target_power, natural_count = group_power_sections(
        result, grades, distances, target_segments=target_segments
    )
    result.update({
        'ftp': ftp,
        'target_if': target_if,
        'if_low': low,
        'if_high': high,
        'achieved_if': achieved_if,
        'if_status': if_status,
        'min_reserve_warn_j': min_reserve_warn_j,
        'grades': grades,
        'distances_m': distances,
        'power_sections': sections,
        'seg_target_power': seg_target_power,
        'natural_section_count': natural_count,
    })
    return result


def optimize_pacing(
    grades,
    distances,
    ftp,
    target_if,
    max_power_w,
    reserve_j,
    cda,
    mass,
    crr,
    eff,
    v_cap,
    min_reserve_warn_j=0.0,
    if_margin=0.01,
    reserve_decay_k=0.20,
    calc_segment_max_m=CALC_SEGMENT_MAX_M,
    descent_mode=DESCENT_MODE_RACE_SPEED,
    climb_cda=None,
    rho=RHO,
    wind=0.0,
    target_segments=None,
):
    """Bisect pacing intensity k under IF target and fatigue-aware durability dynamics."""
    rho_is_arr = np.ndim(rho) > 0
    wind_is_arr = np.ndim(wind) > 0
    if rho_is_arr or wind_is_arr:
        base_grades = np.array(grades, dtype=float)
        base_distances = np.array(distances, dtype=float)
        if rho_is_arr:
            grades, distances, rho = subdivide_course(
                base_grades, base_distances, calc_segment_max_m, rho
            )
        else:
            grades, distances = subdivide_course(
                base_grades, base_distances, calc_segment_max_m
            )
        if wind_is_arr:
            # Re-run on the original geometry so the subdivided wind grid stays
            # index-aligned with the (deterministically) subdivided grades.
            _g, _d, wind = subdivide_course(
                base_grades, base_distances, calc_segment_max_m, wind
            )
    else:
        grades, distances = subdivide_course(grades, distances, calc_segment_max_m)

    ftp = max(1.0, ftp)
    reserve_j = max(1.0, reserve_j)
    target_if = max(0.1, target_if)
    if_margin = max(0.0, if_margin)
    if_low = max(0.0, target_if - if_margin)
    if_high = target_if + if_margin
    base_power = ftp * max(0.1, if_low - 0.05)

    k_lo = 0.0
    k_hi = 10_000_000.0

    result_lo = simulate_reserve_balance(
        grades, distances, ftp, target_if, base_power, k_lo, reserve_j,
        cda, mass, crr, eff, v_cap, max_power_w, reserve_decay_k,
        descent_mode=descent_mode,
        climb_cda=climb_cda,
        rho=rho,
        wind=wind,
    )
    if result_lo is None:
        return None

    result_hi = simulate_reserve_balance(
        grades, distances, ftp, target_if, base_power, k_hi, reserve_j,
        cda, mass, crr, eff, v_cap, max_power_w, reserve_decay_k,
        descent_mode=descent_mode,
        climb_cda=climb_cda,
        rho=rho,
        wind=wind,
    )
    if result_hi is None:
        return _finish_result(result_lo, grades, distances, ftp, target_if, if_margin, min_reserve_warn_j, target_segments)

    if result_lo['np_power'] > ftp * if_high:
        return _finish_result(result_lo, grades, distances, ftp, target_if, if_margin, min_reserve_warn_j, target_segments)

    if result_hi['np_power'] <= ftp * if_high:
        return _finish_result(result_hi, grades, distances, ftp, target_if, if_margin, min_reserve_warn_j, target_segments)

    best_result = result_lo
    target_np_high = ftp * if_high
    for _ in range(30):
        k_mid = (k_lo + k_hi) / 2
        result = simulate_reserve_balance(
            grades, distances, ftp, target_if, base_power, k_mid, reserve_j,
            cda, mass, crr, eff, v_cap, max_power_w, reserve_decay_k,
            descent_mode=descent_mode,
            climb_cda=climb_cda,
            rho=rho,
            wind=wind,
        )
        if result is None:
            k_hi = k_mid
            continue

        if result['np_power'] > target_np_high:
            k_hi = k_mid
        else:
            k_lo = k_mid
            best_result = result

        if result['np_power'] <= target_np_high and abs(result['np_power'] - target_np_high) < max(1.0, ftp * 0.001):
            best_result = result
            break

    return _finish_result(best_result, grades, distances, ftp, target_if, if_margin, min_reserve_warn_j, target_segments)


def aggregate_gradient_bands(sim_result, grades, distances):
    """Aggregate simulation outputs into gradient bands."""
    d_dist = np.diff(distances)
    n = min(len(grades), len(sim_result['seg_power']), len(d_dist))

    bands = []
    for label, lo, hi in GRADIENT_BANDS:
        mask = np.array([(lo <= grades[i] < hi) for i in range(n)])
        dist_m = float(np.sum(d_dist[:n][mask])) if mask.any() else 0.0
        time_s = float(np.sum(sim_result['seg_time'][:n][mask])) if mask.any() else 0.0

        if time_s > 0 and mask.any():
            power = float(np.sum(sim_result['seg_power'][:n][mask] * sim_result['seg_time'][:n][mask]) / time_s)
            speed_kmh = (dist_m / time_s) * 3.6
        else:
            power = 0.0
            speed_kmh = 0.0

        total_dist = float(distances[-1] - distances[0]) if len(distances) > 1 else 1.0
        bands.append({
            'label': label,
            'lo': lo,
            'hi': hi,
            'dist_m': dist_m,
            'pct': dist_m / max(total_dist, 1.0) * 100,
            'power': power,
            'speed_kmh': speed_kmh,
        })

    return bands
