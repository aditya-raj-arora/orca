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
    Configuration is read once in __init__ (same pattern as GeofencingAgent),
    never inside evaluate().
  - Geofencing violations and active severe-weather alerts are non-negotiable:
    they cannot be downgraded by favourable data from other agents (Figure 2).
  - FR-RISK-3 / NFR-REL-2: if ANY contributing agent's result is missing or has
    status='unavailable', the verdict MUST be INSUFFICIENT_DATA — never SAFE,
    regardless of what the other agents say. This is the rule most likely to
    be gotten wrong under time pressure; do not relax it.
"""
from __future__ import annotations

from app.core.config import get_settings
from app.schemas.geofence import GeofenceResult
from app.schemas.ocean import PFZResult
from app.schemas.risk import RiskVerdict
from app.schemas.weather import WeatherResult


class RiskSafetyAgent:
    def __init__(
        self,
        marginal_wind_kmh: float | None = None,
        marginal_wave_m: float | None = None,
    ) -> None:
        # Figure 2's "weather conditions marginal" branch needs thresholds the
        # LLD doesn't fix numerically; they're configurable (RISK_MARGINAL_*),
        # overridable per-instance for fixture tests, and never hardcoded in
        # evaluate() itself.
        settings = get_settings()
        self._marginal_wind_kmh = (
            settings.risk_marginal_wind_kmh if marginal_wind_kmh is None else marginal_wind_kmh
        )
        self._marginal_wave_m = (
            settings.risk_marginal_wave_m if marginal_wave_m is None else marginal_wave_m
        )

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
             severe-weather alerts are non-negotiable, see module docstring).
          5. Else, wind or wave at/above the marginal thresholds -> CAUTION
             (Figure 2's "weather conditions marginal, no active alert?"
             branch). Marginal conditions can only ever soften SAFE; they
             never downgrade an UNSAFE from step 3 or 4, which is why this
             check sits below both.
          6. Else -> SAFE.

        Every verdict that has source data attaches its timestamps (Figure 2's
        final "attach rationale ... + source data timestamps" step, FR-RISK-2)
        so Synthesis can cite how fresh the underlying data was.

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
            return RiskVerdict(
                verdict="UNSAFE",
                rationale=self._annotate(rationale, weather, ocean, pfz_informed_verdict=False),
                contributing_factors=factors,
            )

        if weather.active_alerts:
            factors.extend(weather.active_alerts)
            rationale = "Active weather alert(s): " + "; ".join(weather.active_alerts) + "."
            return RiskVerdict(
                verdict="UNSAFE",
                rationale=self._annotate(rationale, weather, ocean, pfz_informed_verdict=False),
                contributing_factors=factors,
            )

        marginal = self._marginal_factors(weather)
        if marginal:
            factors.extend(marginal)
            rationale = (
                "No active weather alerts, but conditions are marginal: "
                + "; ".join(marginal)
                + "."
            )
            return RiskVerdict(
                verdict="CAUTION",
                rationale=self._annotate(rationale, weather, ocean, pfz_informed_verdict=True),
                contributing_factors=factors,
            )

        rationale = "No geofence violations or active weather alerts for this location."
        return RiskVerdict(
            verdict="SAFE",
            rationale=self._annotate(rationale, weather, ocean, pfz_informed_verdict=True),
            contributing_factors=factors,
        )

    def _marginal_factors(self, weather: WeatherResult) -> list[str]:
        """Figure 2's "marginal conditions" test: moderate wind/wave with no
        active alert. Only reached when both alert sources were reachable and
        reported nothing, so these readings can be trusted as-is."""
        marginal: list[str] = []
        if weather.wind_speed_kmh is not None and weather.wind_speed_kmh >= self._marginal_wind_kmh:
            marginal.append(
                f"wind {weather.wind_speed_kmh:.0f} km/h "
                f"(at or above the {self._marginal_wind_kmh:.0f} km/h caution threshold)"
            )
        if weather.wave_height_m is not None and weather.wave_height_m >= self._marginal_wave_m:
            marginal.append(
                f"wave height {weather.wave_height_m:.1f} m "
                f"(at or above the {self._marginal_wave_m:.1f} m caution threshold)"
            )
        return marginal

    @staticmethod
    def _annotate(
        rationale: str,
        weather: WeatherResult,
        ocean: PFZResult | None,
        pfz_informed_verdict: bool,
    ) -> str:
        """Appends the stale-PFZ caveat and the source data timestamps
        (Figure 2's final "attach rationale" step / FR-RISK-2).

        pfz_informed_verdict distinguishes the two caveat wordings already in
        use: on a non-negotiable UNSAFE the advisory played no part at all,
        whereas on SAFE/CAUTION the reader may well act on it.
        """
        if ocean is not None and ocean.is_stale:
            rationale += (
                " Note: the nearest PFZ advisory is stale — treat it as indicative only."
                if pfz_informed_verdict
                else " (Note: PFZ advisory data is stale and was not used in this verdict.)"
            )

        sources: list[str] = []
        if weather.data_timestamp is not None:
            sources.append(f"weather {weather.data_timestamp.isoformat()}")
        if ocean is not None and ocean.data_timestamp is not None:
            sources.append(f"PFZ advisory {ocean.data_timestamp.isoformat()}")
        if sources:
            rationale += " Source data: " + ", ".join(sources) + "."
        return rationale
