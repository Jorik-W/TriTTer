"""
Physics engine for bike time estimation.
"""

import numpy as np

RHO = 1.2   # air density kg/m³
G   = 9.81  # gravity m/s²


def solve_speed(p_wheel, grade, cda, mass, crr, rho=RHO, wind_ms=0.0):
    """Solve: P = 0.5·ρ·CdA·(v+w)·|v+w|·v + (Crr + grade)·m·g·v.

    ``wind_ms`` is the headwind component along the direction of travel
    (m/s, positive = headwind, negative = tailwind). With ``wind_ms == 0``
    this reduces to the still-air cubic 0.5·ρ·CdA·v³ + (Crr+grade)·m·g·v.
    """
    p_wheel = max(0.0, float(p_wheel))
    grade = float(grade)
    cda = max(1e-6, float(cda))
    mass = max(1.0, float(mass))
    crr = max(0.0, float(crr))
    rho = max(0.1, float(rho))
    wind_ms = float(wind_ms)

    aero = 0.5 * rho * cda
    rolling_gravity = (crr + grade) * mass * G

    def residual(v):
        v_air = v + wind_ms
        return aero * v_air * abs(v_air) * v + rolling_gravity * v - p_wheel

    lo = 0.3
    if residual(lo) >= 0:
        return lo

    hi = 5.0 if grade > 0.01 else (25.0 if grade < -0.01 else 12.0)
    iter_count = 0
    max_iters = 100
    while residual(hi) < 0 and hi < 100.0 and iter_count < max_iters:
        hi *= 1.5
        iter_count += 1

    if residual(hi) < 0:
        return min(hi, 100.0)

    for _ in range(80):
        mid = (lo + hi) / 2
        if residual(mid) < 0:
            lo = mid
        else:
            hi = mid

    return max(hi, 0.3)


def solve_powers(avg_p, np_target, d_pow, t_c, t_f, t_d):
    """
    Given time fractions and descent power, find climb and flat power such that:
      weighted_avg(pC, pF, dPow) = avg_p
      NP(pC, pF, dPow)          = np_target
    """
    t_tot = t_c + t_f + t_d
    if t_tot < 1e-6:
        return avg_p, avg_p

    f_c = t_c / t_tot
    f_f = t_f / t_tot
    f_d = t_d / t_tot

    A = avg_p - f_d * d_pow                        # fC*pC + fF*pF = A
    B = np_target**4 - f_d * d_pow**4              # fC*pC^4 + fF*pF^4 = B
    if not np.isfinite(A) or not np.isfinite(B):
        return max(10.0, avg_p), max(10.0, avg_p)

    if f_c < 1e-6:
        p_f = A / max(f_f, 1e-6)
        return p_f, p_f
    if f_f < 1e-6:
        p_c = A / f_c
        return p_c, avg_p


    def p_c_from_pf(p_f):
        return (A - f_f * p_f) / f_c

    def residual_pf(p_f):
        p_c = p_c_from_pf(p_f)
        return f_c * p_c**4 + f_f * p_f**4 - B

    p_f_min = 10.0
    p_f_max = max(p_f_min + 1.0, (A - 10.0 * f_c) / max(f_f, 1e-6))
    p_f_max = min(2000.0, p_f_max)

    def avg_consistent_equal_power():
        p_eq = A / max(f_c + f_f, 1e-6)
        p_eq = max(10.0, min(2000.0, p_eq))
        return p_eq, p_eq

    if p_f_max <= p_f_min + 1e-6:
        return avg_consistent_equal_power()

    xs = np.linspace(p_f_min, p_f_max, 220)
    ys = [residual_pf(x) for x in xs]
    candidates = []

    # Bisection across all sign-change intervals to capture both quartic branches.
    for i in range(len(xs) - 1):
        x0, x1 = xs[i], xs[i + 1]
        y0, y1 = ys[i], ys[i + 1]
        if not np.isfinite(y0) or not np.isfinite(y1):
            continue
        if y0 == 0.0:
            candidates.append(x0)
            continue
        if y0 * y1 > 0:
            continue

        lo, hi = x0, x1
        flo, fhi = y0, y1
        for _ in range(80):
            mid = 0.5 * (lo + hi)
            fmid = residual_pf(mid)
            if not np.isfinite(fmid) or abs(fmid) < 1e-6:
                lo = hi = mid
                break
            if flo * fmid <= 0:
                hi, fhi = mid, fmid
            else:
                lo, flo = mid, fmid
        candidates.append(0.5 * (lo + hi))

    if not candidates:
        return avg_consistent_equal_power()

    best = None
    for p_f in candidates:
        p_c = p_c_from_pf(p_f)
        if not np.isfinite(p_c) or p_c < 10.0 or p_f < 10.0:
            continue
        err = abs(residual_pf(p_f))
        prefers_climb = 0 if p_c >= p_f else 1
        score = (prefers_climb, err, abs(p_c - p_f))
        if best is None or score < best[0]:
            best = (score, p_c, p_f)

    if best is None:
        return avg_consistent_equal_power()

    _, p_c, p_f = best
    return max(10.0, p_c), max(10.0, p_f)


def _loop_segment_distances(dist_m, elev, up_grad, dn_grad):
    """Compute climb/flat/descent distances for the loop-style classic model."""
    dist_m = max(0.0, float(dist_m))
    elev = max(0.0, float(elev))
    up_grad = max(1e-4, float(up_grad))
    dn_grad = max(1e-4, float(dn_grad))

    dist_climb = elev / up_grad
    dist_desc = elev / dn_grad
    raw_total = dist_climb + dist_desc

    if raw_total <= dist_m + 1e-6:
        dist_flat = max(0.0, dist_m - raw_total)
        return dist_climb, dist_flat, dist_desc

    # Keep loop semantics but avoid impossible over-allocation of segment distance.
    scale = dist_m / max(raw_total, 1e-9)
    dist_climb *= scale
    dist_desc *= scale
    return dist_climb, 0.0, dist_desc


def _classic_descent_power(ftp, dn_grad_pct):
    """Strict-principles descent power from FTP and average descent grade.
    Uses midpoints of the race-speed bounds table at assumed mid-race reserve.
    """
    g = abs(float(dn_grad_pct))
    if g < 1.0:
        return ftp * 0.62
    if g < 2.0:
        return ftp * 0.55
    if g < 4.0:
        return ftp * 0.40
    if g < 6.0:
        return ftp * 0.20
    if g < 8.0:
        return ftp * 0.05
    return 0.0


def estimate_time(params, fit_data=None):
    """
    Core estimation. Returns dict with results and segment breakdown.
    If fit_data provided, uses real gradient distribution from FIT file.

    Flat power is fixed at avg_power. Descent power is derived from FTP
    and descent gradient. Climb power is solved so that weighted NP = FTP * if_target.
    """
    dist_m   = params['dist_km'] * 1000
    elev     = params['elev_m']
    avg_p    = params['avg_power']
    ftp      = params.get('ftp', avg_p / 0.65)  # backward-compat fallback
    if_tgt   = params.get('if_target', 0.75)
    cda      = params['cda']
    climb_cda = params.get('climb_cda', cda)
    mass     = params['mass_kg']
    crr      = params['crr']
    eff      = params['drivetrain_eff']
    rho      = params.get('rho', RHO)
    up_grad  = params['climb_grad'] / 100
    dn_grad  = params['desc_grad'] / 100
    v_cap    = params['desc_speed_cap'] / 3.6

    if fit_data and fit_data.get('valid'):
        elev     = fit_data['total_elevation']
        dist_m   = fit_data['total_distance']
        up_grad  = fit_data['mean_climb_grad']
        dn_grad  = fit_data['mean_desc_grad']

    # Segment road distances (loop-style decomposition).
    dist_climb, dist_flat, dist_desc = _loop_segment_distances(dist_m, elev, up_grad, dn_grad)

    # Flat power fixed; descent power from strict-principles table; target NP = FTP * IF.
    p_f = avg_p
    target_np = ftp * max(0.5, float(if_tgt))
    d_pow = _classic_descent_power(ftp, abs(dn_grad) * 100)
    p_wheel_desc = d_pow * eff

    # Initial time estimate at avg power.
    v_f = solve_speed(p_f * eff, 0.0, cda, mass, crr, rho)
    v_d = min(solve_speed(p_wheel_desc, -dn_grad, cda, mass, crr, rho), v_cap)
    v_c = solve_speed(p_f * eff, up_grad, climb_cda, mass, crr, rho)  # seed

    t_c = dist_climb / max(v_c, 0.1)
    t_f = dist_flat  / max(v_f, 0.1)
    t_d = dist_desc  / max(v_d, 0.1)

    # Iterate: solve p_c analytically, then update climb time.
    p_c = avg_p
    for _ in range(25):
        t_tot = max(t_c + t_f + t_d, 1e-9)
        f_c = t_c / t_tot
        f_f = t_f / t_tot
        f_d = t_d / t_tot

        rhs = target_np ** 4 - f_f * p_f ** 4 - f_d * d_pow ** 4
        if rhs > 0.0 and f_c > 1e-9:
            p_c = (rhs / f_c) ** 0.25
        else:
            p_c = p_f  # fallback: no climb power boost possible

        p_c = max(10.0, min(2000.0, p_c))
        v_c_new = solve_speed(p_c * eff, up_grad, climb_cda, mass, crr, rho)
        t_c_new = dist_climb / max(v_c_new, 0.1)

        if abs(t_c_new - t_c) < 0.1:
            t_c = t_c_new
            v_c = v_c_new
            break
        t_c = t_c_new
        v_c = v_c_new

    t_total = t_c + t_f + t_d
    if t_total < 1:
        return None

    avg_speed = (dist_m / t_total) * 3.6
    t_tot = t_c + t_f + t_d
    np_check  = (t_c/t_tot * p_c**4 + t_f/t_tot * p_f**4 + t_d/t_tot * d_pow**4) ** 0.25
    avg_check = (t_c * p_c + t_f * p_f + t_d * d_pow) / t_tot

    return {
        'time_h':      t_total / 3600,
        'avg_speed':   avg_speed,
        'wkg':         avg_p / mass,
        'np_check':    np_check,
        'avg_check':   avg_check,
        'segments': [
            {'name': 'Climbing', 'dist_km': dist_climb/1000, 'speed': v_c*3.6, 'time_h': t_c/3600, 'power': p_c},
            {'name': 'Flat',     'dist_km': dist_flat /1000, 'speed': v_f*3.6, 'time_h': t_f/3600, 'power': p_f},
            {'name': 'Descent',  'dist_km': dist_desc /1000, 'speed': v_d*3.6, 'time_h': t_d/3600, 'power': d_pow},
        ]
    }
