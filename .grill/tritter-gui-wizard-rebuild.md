# Grill: TriTTer GUI wizard rebuild + full Plan port
Date: 2026-06-29

## Intent
Merge `cda_analyzer` (recorded ride → CdA analysis) and `bike_estimator` (course →
pacing/time/power, FIT export) into one program, **TriTTer**, with a single uniform GUI.
Combine `cda_analyzer`'s compactness with `bike_estimator`'s flat dark style. Every
input gets a slider + input-box pair, laid out compactly, and the user is walked
through the settings step by step.

## Constraints
- Use `cda_analyzer` as the base.
- Keep the already-built 3-tab shell and multi-rider Profile system (single source of truth).
- Don't rewrite working plots onto a new engine — visual uniformity only.
- Fresh virtual environment, Python 3.14, isolated to the merged repo.

## Key decisions
- Decision: GUI uses a **true wizard** (one step per screen, Next/Back). Reason: user
  wants an explicit guided walkthrough. Alternative considered: numbered collapsible
  sections in one scrollable tab (rejected — not "step-wise" enough).
- Decision: Wizard's **final step is a live, interactive Results screen** with a
  **clickable step rail** so any step is one click away. Reason: preserve
  `cda_analyzer`'s fast tweak-and-re-run loop. Alternative: strict linear Back/Next
  (rejected — too slow to iterate).
- Decision: **Keep 3 top-level tabs** (Profile / Analyze / Plan); the wizard lives
  inside Analyze and inside Plan. **Profile stays a dense form**, not a wizard. Reason:
  Profile is reference/data-entry, not a sequential task. Alternative: one app-wide
  wizard with no tabs (rejected).
- Decision: **Full Plan port lands this pass**, sequenced after Analyze. Reason: user
  said "implement all original task". Build the reusable wizard on the working Analyze
  first to validate the pattern, then port `bike_estimator` (course/physics/durability/
  fit_export, pyqtgraph, ADB) into a second wizard reusing the same widgets.
- Decision: **Uniformity = one dark-flat theme + shared input widgets + one layout
  grammar**; plotting engines stay per-tab (matplotlib+folium for Analyze, pyqtgraph
  for Plan). Reason: theme/widgets are cheap and high-value; unifying plot engines is a
  risky rewrite not required for visual uniformity.
- Decision: **File granularity = one `.py` per wizard step** under `analyze/steps/` and
  `plan/steps/`, on top of a shared `ui/` framework (`theme.py`, `widgets.py`,
  `wizard.py`). Reason: matches "one .py per step" without duplicating slider/theme code.
- Decision: **Fresh `TriTTer/.venv`, Python 3.14**, with a merged `requirements.txt`
  (union of both projects). Reason: clean isolation; ships with the merged repo.

## Surfaced assumptions
- The slider+spinbox widget the user wants already exists as `SliderRow` in
  `plan/ui/widgets.py` — it gets promoted to the shared `ui/` framework, not rewritten.
- "Uniform" means consistent theme/widgets/layout, not a single plotting engine.
- Rider selection stays a one-liner at the top of each wizard, sourced from Profile.

## Open questions
- PyQt5 5.15 wheel availability on Python 3.14 is unverified. If wheels don't resolve,
  fall back to PyQt6 or a Python 3.12 interpreter — to be confirmed empirically.

## Out of scope
- Rewriting plots/maps onto a single plotting engine.
- Making the Profile tab a wizard.
- Session persistence beyond the existing profiles JSON.
