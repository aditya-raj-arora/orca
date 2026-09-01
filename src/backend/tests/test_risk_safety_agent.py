"""
Fixture-based unit tests for RiskSafetyAgent.evaluate() — the single most
safety-critical function in the system (see risk_safety_agent.py docstring).

Owner: P4 (Geospatial & Risk Engineer).
Reference: LLD v1.0 §2.6 ("independently unit-testable"), CONTRIBUTING.md
Definition of Done.

TODO(P4): flesh out the five minimum cases below once WeatherResult /
GeofenceResult / RiskSafetyAgent are implemented. These are currently
`xfail`-marked stubs so the suite documents required coverage without
blocking CI before the agent exists.
"""
import pytest

pytestmark = pytest.mark.xfail(
    reason="RiskSafetyAgent.evaluate() not yet implemented — see app/agents/risk_safety_agent.py TODOs",
    strict=False,
)


def test_all_clear_returns_safe():
    ...


def test_weather_alert_only_returns_caution_or_unsafe():
    ...


def test_geofence_violation_is_unsafe_even_with_clear_weather():
    """FR-GEO-4 / NFR-REL-2 regression guard: a geofence violation must never
    be downgraded by favourable weather data."""
    ...


def test_missing_weather_returns_insufficient_data_not_safe():
    """FR-RISK-3 / NFR-REL-2 regression guard: never default to SAFE on
    missing data."""
    ...


def test_missing_geofence_returns_insufficient_data_not_safe():
    ...
