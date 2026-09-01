"""
Risk / Safety Agent — combines Weather, Ocean, and Geofencing outputs into one
composite, explainable safety verdict.

Owner: P4 (Geospatial & Risk Engineer).
Implements: FR-RISK-1 to FR-RISK-3.
Reference: LLD v1.0 §2.6, §4.2 / Figure 2 (docs/ORCA_LLD_v1.0.docx has the
full flowchart — read it before implementing this, do not improvise the tree).

THIS IS THE SAFETY-CRITICAL COMPONENT OF THE WHOLE SYSTEM.
  - evaluate() MUST be a pure function: no external I/O, no LLM call. It must
    be deterministic and independently unit-testable against fixed fixtures
    (LLD §2.6) — write those fixture tests before merging, not after.
  - Geofencing violations and active severe-weather alerts are non-negotiable:
    they cannot be downgraded by favourable data from other agents (Figure 2).
  - FR-RISK-3 / NFR-REL-2: if ANY contributing agent's result is missing or has
    status='unavailable', the verdict MUST be INSUFFICIENT_DATA — never SAFE,
    regardless of what the other agents say. This is the rule most likely to
    be gotten wrong under time pressure; do not relax it.
"""
from __future__ import annotations

from app.schemas.geofence import GeofenceResult
from app.schemas.ocean import OceanParams
from app.schemas.risk import RiskVerdict
from app.schemas.weather import WeatherResult


class RiskSafetyAgent:
    def evaluate(
        self,
        weather: WeatherResult | None,
        geofence: GeofenceResult | None,
        ocean: OceanParams | None,
    ) -> RiskVerdict:
        """
        TODO(P4): implement the decision tree from LLD §4.2 / Figure 2. Rough
        shape (confirm exact order/precedence against the actual flowchart in
        docs/ORCA_LLD_v1.0.docx before coding):
          1. If weather is None or weather.status == 'unavailable' -> return
             INSUFFICIENT_DATA (a safety-relevant input is missing).
          1a. P1/P3 contract-lock addendum (2026-09-01): also treat
              weather.alerts_source_available == False as this same case.
              Both alert sources failed, so weather.active_alerts == [] is
              "unknown", not "no alerts" — do NOT let it fall through to the
              alerts-severity branch (step 4) as if it were trustworthy.
              INSUFFICIENT_DATA, never SAFE, per NFR-REL-2.
          2. Same check for geofence (geofence is the only agent whose absence
             on its own should probably be treated as UNSAFE-leaning rather
             than merely "insufficient" if a query is boundary-relevant —
             confirm against Figure 2 exactly, do not assume).
          3. If geofence.within_mpa or geofence.within_imbl_buffer -> verdict
             is UNSAFE, unconditionally (FR-GEO-4) — this check must run
             before, and cannot be overridden by, weather being "clear".
          4. Else combine weather.active_alerts severity into
             SAFE/CAUTION/UNSAFE per the thresholds in Figure 2.
          5. Populate `rationale` (human-readable, FR-RISK-2) and
             `contributing_factors` (list of the specific inputs that drove
             the verdict, e.g. ["within 3km of MPA 'Gulf of Mannar'"]).

        Unit test this against fixtures covering: all-clear, weather-only
        alert, geofence-only violation, both, and each individual agent
        reporting unavailable — five minimum cases before this is "done"
        per CONTRIBUTING.md's Definition of Done.
        """
        raise NotImplementedError
