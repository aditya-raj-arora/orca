"""
Fixture-based unit tests for RiskSafetyAgent.evaluate() — the single most
safety-critical function in the system (see risk_safety_agent.py docstring).

Owner: P4 (Geospatial & Risk Engineer).
Reference: LLD v1.0 §2.6 ("independently unit-testable"), CONTRIBUTING.md
Definition of Done.
"""
from datetime import UTC, datetime

from app.agents.risk_safety_agent import RiskSafetyAgent
from app.schemas.geofence import GeofenceResult
from app.schemas.ocean import PFZResult
from app.schemas.weather import WeatherResult

_NOW = datetime(2026, 9, 3, tzinfo=UTC)


def _weather(**overrides) -> WeatherResult:
    defaults = dict(
        wind_speed_kmh=15.0,
        wave_height_m=1.0,
        active_alerts=[],
        data_timestamp=_NOW,
        status="ok",
        alerts_source_available=True,
    )
    defaults.update(overrides)
    return WeatherResult(**defaults)


def _geofence(**overrides) -> GeofenceResult:
    defaults = dict(
        within_imbl_buffer=False,
        imbl_distance_km=42.0,
        within_mpa=False,
        mpa_name=None,
    )
    defaults.update(overrides)
    return GeofenceResult(**defaults)


def test_all_clear_returns_safe():
    verdict = RiskSafetyAgent().evaluate(_weather(), _geofence(), None)
    assert verdict.verdict == "SAFE"


def test_weather_alert_only_returns_caution_or_unsafe():
    verdict = RiskSafetyAgent().evaluate(
        _weather(active_alerts=["Cyclone warning"]), _geofence(), None
    )
    assert verdict.verdict in ("CAUTION", "UNSAFE")
    assert "Cyclone warning" in verdict.contributing_factors


def test_geofence_violation_is_unsafe_even_with_clear_weather():
    """FR-GEO-4 / NFR-REL-2 regression guard: a geofence violation must never
    be downgraded by favourable weather data."""
    verdict = RiskSafetyAgent().evaluate(
        _weather(),
        _geofence(within_mpa=True, mpa_name="Gulf of Mannar"),
        None,
    )
    assert verdict.verdict == "UNSAFE"


def test_missing_weather_returns_insufficient_data_not_safe():
    """FR-RISK-3 / NFR-REL-2 regression guard: never default to SAFE on
    missing data."""
    verdict = RiskSafetyAgent().evaluate(None, _geofence(), None)
    assert verdict.verdict == "INSUFFICIENT_DATA"


def test_missing_geofence_returns_insufficient_data_not_safe():
    verdict = RiskSafetyAgent().evaluate(_weather(), None, None)
    assert verdict.verdict == "INSUFFICIENT_DATA"


def test_unavailable_weather_status_returns_insufficient_data():
    verdict = RiskSafetyAgent().evaluate(
        _weather(status="unavailable"), _geofence(), None
    )
    assert verdict.verdict == "INSUFFICIENT_DATA"


def test_alerts_source_unavailable_returns_insufficient_data_not_safe():
    """P1/P3 contract-lock addendum (2026-09-01) / NFR-REL-2 regression
    guard: alerts_source_available=False means active_alerts == [] is
    "unknown", not "clear" — must not fall through to SAFE."""
    verdict = RiskSafetyAgent().evaluate(
        _weather(active_alerts=[], alerts_source_available=False),
        _geofence(),
        None,
    )
    assert verdict.verdict == "INSUFFICIENT_DATA"
    assert verdict.verdict != "SAFE"


def test_alerts_source_unavailable_overrides_apparent_all_clear_geofence_too():
    """A downed-alerts WeatherResult can otherwise look completely healthy
    (wind/wave/timestamp all present) — this must still short-circuit before
    the geofence/alerts branches, even when geofence is also clean."""
    verdict = RiskSafetyAgent().evaluate(
        _weather(alerts_source_available=False),
        _geofence(within_mpa=False, within_imbl_buffer=False),
        None,
    )
    assert verdict.verdict == "INSUFFICIENT_DATA"


def test_stale_pfz_advisory_is_noted_but_does_not_change_verdict():
    """PFZ is advisory-only (LLD §4.2) — staleness is caveated, never a
    reason to change SAFE/UNSAFE/INSUFFICIENT_DATA."""
    stale_pfz = PFZResult(
        centroid=None, distance_km=10.0, bearing_deg=90.0,
        data_timestamp=_NOW, is_stale=True,
    )
    verdict = RiskSafetyAgent().evaluate(_weather(), _geofence(), stale_pfz)
    assert verdict.verdict == "SAFE"
    assert "stale" in verdict.rationale.lower()
