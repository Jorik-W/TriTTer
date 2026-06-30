# Grill: Open File / Weather restructure (de-wizard Analyse, unify inputs)
Date: 2026-06-29

## Intent
Restructure TriTTer so file loading and weather are first-class shared tabs, the
Analyse wizard is removed, and Analyse/Plan become results-first. Eliminate
duplicate file-loading code and standardize every slider on one widget.

## Tab structure (decided)
Top tabs (left→right): **Open File | Weather | Profile | Analyse | Plan**

## Where inputs live (decided)
- **Profile:** rider/equipment + power constants — mass, CdA, climbing CdA, Crr,
  drivetrain, **FTP, Max power**. Single source of truth.
- **Open File:** the shared file picker (FIT/GPX) loaded once for both modes.
- **Weather:** all weather/conditions + the wind-effect factor (see below).
- **Analyse (results):** results-first; Analyse-only knobs in a collapsible
  "Advanced" strip — segment detection (min length/duration/speed/max speed),
  steady-state thresholds (speed/power), CdA keep %. Keeps an explicit
  **Run/Refresh Analyse** button (full CdA analysis is costly).
- **Plan (results):** results-first; live auto-compute (debounced, no button).
  Keeps **manual mode** (course sliders: distance/elevation/climb grad/descent
  grad/speed cap) so Plan works with no file. Plan-only input kept in Plan:
  **IF slider**. Power targets come from Profile (FTP + Max power) × IF.

## Weather tab (decided)
- Source selector: **(•) Manual  ( ) From API**.
  - Manual: temp, pressure, humidity, wind speed, wind direction — **each as
    slider + input box** (the shared SliderRow).
  - API: Time = **(•) From file's start time  ( ) Pick date/time**. forecast vs
    history chosen **automatically** from that time. Show a **text box with API
    results** and a **Fetch weather** button. Clearly flag when the requested
    time has no data (term TBD — see open questions).
- **wind_effect_factor**: ONE shared global here. Range **0.00–1.50**, default
  **0.40**. Applies to **both** Analyse and Plan.
- "Applies to: ☑ Analyse ☑ Plan" — one shared weather config, toggleable per mode.

## wind_effect_factor — corrected meaning (decided)
NOT a yaw calibration. It is a **10 m → ground wind attenuation/exposure factor**:
converts the API's 10 m wind to the effective ground-level wind the rider feels
(urban/forest shelter → low; open field → high). Therefore it is an
**environmental/conditions** property, not a rider property.
- Decision: **(a)** move it OUT of Profile → single global in the Weather tab
  (default 0.40 for all). Per-rider seed values (0.05–0.10) dropped.
- **Plan implementation:** multiply the 10 m-derived route headwind by `wef`
  before the optimizer (currently Plan applies full physical wind, no factor).

## File loader merge (decided)
Two parsers exist today: `analyze/fit_parser.py` (FITParser → power/speed/cad/HR/
GPS/alt/time) and `plan/course.py` (`parse_fit_file`/`parse_course_file` →
geometry: lat/lon/alt/distance/grades/time, + GPX).
- Decision: **one shared loader in `core/`** reads FIT/GPX **once** into a unified
  course object (time, lat, lon, altitude, distance, speed, power, hr, cadence,
  grades; missing fields → None). Analyse + Plan consume what they need.
  Refactor both existing parsers to call it (behavior-preserving).
- **Gating by available fields:**
  - **GPX → Plan only** (no power).
  - **FIT with power + speed → both.**
  - **FIT without power → Plan only**, and Analyse tab shows a useful message
    + is disabled.
  - Analyse required set = **power + speed + (GPS/altitude)**.

## Titlebar (decided)
Color the OS titlebar to match the app: **(a) DWM API** —
`DWMWA_USE_IMMERSIVE_DARK_MODE` (dark caption Win10/11) +
`DWMWA_CAPTION_COLOR` = exact `#1E1E2E` (Win11 22000+). Native min/max/close +
snap kept. Degrades to dark caption on Win10. (Rejected: frameless custom
titlebar — too much fragile code.)

## Slider unification (decided)
**Delete Plan's `SliderRow`** (`plan/planui/widgets.py`) and standardize every
slider on the **one shared `SliderRow`** (`ui/widgets.py`): Plan, Analyse, Open
File, Weather all identical. Adapt `plan/planui/advanced_tab.py` to the shared API.

## Key decisions (summary)
- Decision: 5 tabs, Analyse/Plan results-first. Reason: declutter, single input
  hubs. Alt rejected: keep wizard.
- Decision: one shared `wind_effect_factor` (10 m→ground), global in Weather,
  both modes. Reason: it's environmental, not rider/mode-specific. Alt rejected:
  two per-mode factors (was based on my wrong "yaw" assumption).
- Decision: one loader in core/. Reason: kill duplicate FIT/GPX parsing. Alt
  rejected: leave two parsers.
- Decision: Analyse keeps Run button; Plan stays live. Reason: full CdA analysis
  is expensive.
- Decision: DWM titlebar. Reason: exact color where supported, minimal code.
- Decision: single shared SliderRow. Reason: literal "exact same slider" request.

## Surfaced assumptions
- `wef` was assumed (by me) to be a yaw fudge factor; user corrected: it's a
  10 m→ground exposure factor. This flipped it from per-mode to shared global.
- Plan currently applies 100% physical wind with no factor — adding `wef`
  changes Plan results.
- "results only" does not mean specialized params vanish — they move to a
  collapsible Advanced strip (Analyse) / manual mode (Plan).
- Profile must gain a **Max power** field (does not exist yet; only `ftp`).

## Open questions (deferred to implementation)
- Better term for "weather time has no data / out of range". Candidates:
  "No weather data for this time", "Outside available weather window",
  "Selected time unavailable". Pick during build.
- Exact relationship FTP + Max power + IF → Plan target power(s) (how the
  durability model consumes Max power). Confirm when wiring Plan inputs.
- Whether manual Plan course sliders live in the Plan Advanced strip or in the
  Open File tab when no file is loaded.

## Out of scope
- Frameless custom titlebar.
- Per-mode / per-rider wind factors.
- Re-theming Plan as a wizard (Plan stays its current dense layout).
