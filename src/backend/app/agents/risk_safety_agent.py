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
from app.schemas.ocean import PFZResult
from app.schemas.risk import RiskVerdict
from app.schemas.weather import WeatherResult


class RiskSafetyAgent:
    def evaluate(
        self,
        weather: WeatherResult | None,
        geofence: GeofenceResult | None,
        ocean: PFZResult | None,
    ) -> RiskVerdict:
        """Decision tree per LLD §4.2 / Figure 2.

          1. weather missing/unavailable, OR weather.alerts_source_available
             is False (P1/P3 contract-lock addendum, 2026-09-01 — both alert
             sources down means active_alerts == [] is "unknown", not "no
             alerts", so it must never fall through to step 4 as if it were
             trustworthy) -> INSUFFICIENT_DATA, never SAFE (NFR-REL-2).
          2. geofence missing -> INSUFFICIENT_DATA (FR-RISK-3/NFR-REL-2: any
             missing contributing agent forces INSUFFICIENT_DATA).
          3. geofence.within_mpa or geofence.within_imbl_buffer -> UNSAFE,
             unconditionally (FR-GEO-4) — cannot be overridden by weather.
          4. Else, weather.active_alerts non-empty -> UNSAFE (active
             severe-weather alerts are non-negotiable, see module docstring);
             otherwise SAFE.

        ocean (PFZResult | None) is advisory-only (LLD §4.2): its absence
        never affects the verdict, but a stale advisory is surfaced in the
        rationale so Synthesis can caveat it.
        """
        factors: list[str] = []

        if weather is None or weather.status == "unavailable":
            return RiskVerdict(
                verdict="INSUFFICIENT_DATA",
                rationale="Weather data is unavailable, so a safety verdict cannot be given.",
                contributing_factors=["weather data unavailable"],
            )

        if not weather.alerts_source_available:
            return RiskVerdict(
                verdict="INSUFFICIENT_DATA",
                rationale=(
                    "Both severe-weather alert sources are currently unreachable, so "
                    "whether any alerts are active cannot be confirmed."
                ),
                contributing_factors=["weather alert sources unavailable"],
            )

        if geofence is None:
            return RiskVerdict(
                verdict="INSUFFICIENT_DATA",
                rationale="Geofence data is unavailable, so a safety verdict cannot be given.",
                contributing_factors=["geofence data unavailable"],
            )

        if geofence.within_mpa or geofence.within_imbl_buffer:
            if geofence.within_mpa:
                factors.append(f"within Marine Protected Area '{geofence.mpa_name}'")
            if geofence.within_imbl_buffer:
                factors.append(
                    f"within {geofence.imbl_distance_km:.1f} km of the IMBL buffer zone"
                )
            rationale = (
                "Location is " + " and ".join(factors)
                + " — this is a non-negotiable safety boundary."
            )
            if ocean is not None and ocean.is_stale:
                rationale += (
                    " (Note: PFZ advisory data is stale and was not used in this verdict.)"
                )
            return RiskVerdict(verdict="UNSAFE", rationale=rationale, contributing_factors=factors)

        if weather.active_alerts:
            factors.extend(weather.active_alerts)
            rationale = "Active weather alert(s): " + "; ".join(weather.active_alerts) + "."
            if ocean is not None and ocean.is_stale:
                rationale += " (Note: PFZ advisory data is stale and was not used in this verdict.)"
            return RiskVerdict(verdict="UNSAFE", rationale=rationale, contributing_factors=factors)

        rationale = "No geofence violations or active weather alerts for this location."
        if ocean is not None and ocean.is_stale:
            rationale += " Note: the nearest PFZ advisory is stale — treat it as indicative only."
        return RiskVerdict(verdict="SAFE", rationale=rationale, contributing_factors=factors)
