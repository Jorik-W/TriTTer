# TriTTer

A unified cycling toolkit that merges two previously separate tools:

- **Analyze (CdA)** — from `cda_analyzer`: load a *recorded* FIT ride and estimate
  aerodynamic drag (CdA) per steady segment, with weather/elevation enrichment,
  matplotlib plots and a folium map.
- **Plan (Pacing)** — from `bike_estimator`: load a *course* (FIT/GPX) and estimate
  time/power with a fatigue-aware durability model, then export a power-course FIT
  and optionally push it to a Hammerhead bike computer (Android device) via ADB.

Both modes share one core (weather, air density, FIT parsing, physics) and a single
**Profile** that owns rider parameters.

## Architecture

```
src/
  main.py            Entry point (GUI default; CLI via --cli --mode analyze|plan)
  core/              Shared core
    air.py           Humidity-corrected air-density model (canonical)
    weather.py       Open-Meteo weather service (delegates density to air.py)
    config.py        Default parameters + API endpoints
    profiles.py      Rider profiles (single source of truth, JSON persistence)
  analyze/           Analyze (CdA) mode (from cda_analyzer)
    qt_gui.py, analyzer.py, fit_parser.py, elevation.py, segment_splitter.py,
    cli.py, utils.py, icon.py, icons/
  plan/              Plan (pacing) mode (from bike_estimator) - wired in Phase 2
    course.py, physics.py, durability_model.py, fit_export.py, weather_plan.py, ui/
  ui/                Top-level shell
    app_shell.py     Three-tab window (Profile / Analyze / Plan)
    profile_tab.py   Rider profile editor
data/                Sample FIT rides
pc/                  Prebuilt sample power courses
platform-tools/      ADB tools for pushing courses to a device (Plan mode)
```

### The three tabs

| Tab | Purpose |
|-----|---------|
| **Profile** | Manage multiple riders (CdA, mass, Crr, drivetrain, wind factor, FTP). Single source of truth; persists to `~/.tritter/profiles.json`. |
| **Analyze** | Compute CdA from a recorded ride. A rider is selected here (or in Profile); a measured CdA can be saved back into the active profile. |
| **Plan** | (Phase 2) Estimate time/power and optimize pacing for a course using the selected rider's parameters. |

Rider selection is shared between Profile and Analyze and kept in sync.

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run

```powershell
# GUI (default)
python src/main.py

# Analyze CLI
python src/main.py --cli --mode analyze --file data/your_ride.fit

# Plan CLI (Phase 2)
python src/main.py --cli --mode plan
```

## Merge notes (key decisions)

- **Air density:** unified on the humidity-corrected model from `bike_estimator`
  (`core/air.py`); the Analyze weather service now delegates to it while keeping its
  route prefetch/caching.
- **Plotting:** Analyze keeps matplotlib + folium; Plan keeps pyqtgraph and will add a
  folium map. Both modes get an elevation graph. matplotlib is Analyze-only.
- **Profiles** seeded from the measured CdA values documented in the original
  `cda_analyzer` config (Jorik, Sam, Xiano, Lars).
