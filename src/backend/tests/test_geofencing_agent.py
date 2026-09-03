"""
Unit tests for GeofencingAgent (issue #16, FR-GEO-1..4).

Owner: P4. No network/DB/PostGIS: the agent takes any GISBoundaryAdapter, so
these use a fake adapter serving synthetic IMBL/MPA GeoJSON — real-world
proportions, not real coordinates, to keep the fixtures self-explanatory.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.agents.geofencing_agent import GeofencingAgent
from app.data_access.base import AdapterResult
from app.schemas.common import LatLon

# A large rectangle standing in for the EEZ boundary (IMBL), lon/lat degrees.
_IMBL_SQUARE = {
    "type": "Polygon",
    "coordinates": [[[70, 0], [90, 0], [90, 20], [70, 20], [70, 0]]],
}

# A small rectangle inside the IMBL square, standing in for one MPA.
_MPA_SQUARE = {
    "type": "Polygon",
    "coordinates": [[[79, 9], [80, 9], [80, 10], [79, 10], [79, 9]]],
}

# A second MPA hugging the IMBL boundary itself, for the "both true" case.
_MPA_NEAR_BOUNDARY = {
    "type": "Polygon",
    "coordinates": [[[89, 9], [90, 9], [90, 10], [89, 10], [89, 9]]],
}

_BOUNDARIES = {
    "boundaries": [
        {"type": "IMBL", "name": "Test EEZ boundary", "geometry": _IMBL_SQUARE, "source": "test"},
        {"type": "MPA", "name": "Test MPA", "geometry": _MPA_SQUARE, "source": "test"},
    ]
}


class _FakeAdapter:
    def __init__(self, result: AdapterResult) -> None:
        self._result = result

    def fetch(self, params: dict) -> AdapterResult:  # noqa: ARG002
        return self._result


def _ok_adapter() -> _FakeAdapter:
    return _FakeAdapter(AdapterResult(data=_BOUNDARIES, fetched_at=datetime.now(UTC), status="ok"))


def test_point_well_inside_boundary_and_outside_mpa_is_clear():
    agent = GeofencingAgent(_ok_adapter())
    result = agent.check(LatLon(lat=15, lon=75))
    assert result.within_mpa is False
    assert result.mpa_name is None
    assert result.within_imbl_buffer is False
    assert result.imbl_distance_km > 5.0


def test_point_inside_mpa_returns_mpa_name():
    agent = GeofencingAgent(_ok_adapter())
    result = agent.check(LatLon(lat=9.5, lon=79.5))
    assert result.within_mpa is True
    assert result.mpa_name == "Test MPA"


def test_point_near_imbl_boundary_is_within_buffer():
    """~0.01 deg from the boundary at the equator is ~1.1 km — well inside
    the default 5 km buffer (IMBL_BUFFER_KM, .env.example)."""
    agent = GeofencingAgent(_ok_adapter())
    result = agent.check(LatLon(lat=10, lon=89.99))
    assert result.within_imbl_buffer is True
    assert result.imbl_distance_km < 5.0


def test_point_far_from_imbl_boundary_is_outside_buffer():
    agent = GeofencingAgent(_ok_adapter())
    result = agent.check(LatLon(lat=10, lon=80))
    assert result.within_imbl_buffer is False


def test_mpa_violation_and_imbl_buffer_can_both_be_true():
    """FR-GEO-4: neither check suppresses the other."""
    boundary_mpa = {
        "type": "MPA", "name": "Boundary MPA", "geometry": _MPA_NEAR_BOUNDARY, "source": "test",
    }
    boundaries = {"boundaries": [_BOUNDARIES["boundaries"][0], boundary_mpa]}
    adapter = _FakeAdapter(
        AdapterResult(data=boundaries, fetched_at=datetime.now(UTC), status="ok")
    )
    agent = GeofencingAgent(adapter)
    result = agent.check(LatLon(lat=9.5, lon=89.99))
    assert result.within_mpa is True
    assert result.within_imbl_buffer is True


def test_unavailable_adapter_data_raises_not_a_degraded_result():
    """No 'unavailable' status exists on GeofenceResult (see
    schemas/geofence.py) — graph.py's _call_bounded relies on an exception
    here to substitute `unavailable=None`, which RiskSafetyAgent treats as
    INSUFFICIENT_DATA."""
    adapter = _FakeAdapter(
        AdapterResult(data=None, fetched_at=datetime.now(UTC), status="unavailable")
    )
    agent = GeofencingAgent(adapter)
    with pytest.raises(RuntimeError):
        agent.check(LatLon(lat=10, lon=80))


def test_missing_imbl_feature_raises():
    adapter = _FakeAdapter(
        AdapterResult(
            data={"boundaries": [_BOUNDARIES["boundaries"][1]]},  # MPA only
            fetched_at=datetime.now(UTC),
            status="ok",
        )
    )
    agent = GeofencingAgent(adapter)
    with pytest.raises(RuntimeError):
        agent.check(LatLon(lat=10, lon=80))


def test_geometry_is_cached_across_calls():
    """Boundary data is reference/slow-changing (HLD §4.1) — the adapter
    should only be hit once per agent instance."""
    calls = {"n": 0}

    class _CountingAdapter(_FakeAdapter):
        def fetch(self, params: dict) -> AdapterResult:  # noqa: ARG002
            calls["n"] += 1
            return self._result

    agent = GeofencingAgent(_CountingAdapter(AdapterResult(
        data=_BOUNDARIES, fetched_at=datetime.now(UTC), status="ok"
    )))
    agent.check(LatLon(lat=15, lon=75))
    agent.check(LatLon(lat=9.5, lon=79.5))
    assert calls["n"] == 1
