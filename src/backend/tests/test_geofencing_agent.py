"""
GeofencingAgent tests against the real bundled boundary dataset.

Owner: P4 (Geospatial & Risk Engineer).
Implements: FR-GEO-1 to FR-GEO-4; regression coverage for #99.
"""
import pytest

from app.agents.geofencing_agent import GeofencingAgent
from app.data_access import gis_boundary_adapter
from app.data_access.gis_boundary_adapter import GISBoundaryAdapter
from app.schemas.common import LatLon

agent = GeofencingAgent(GISBoundaryAdapter())


@pytest.fixture(autouse=True)
def _clear_boundary_cache():
    """The parsed GeoJSON is cached at module scope, so a test that points the
    adapter at a missing file would otherwise be served the previous test's
    successful load (or poison the next one)."""
    with gis_boundary_adapter._CACHE_LOCK:
        gis_boundary_adapter._CACHE = None
    yield
    with gis_boundary_adapter._CACHE_LOCK:
        gis_boundary_adapter._CACHE = None


def test_point_inside_sundarbans_mpa():
    result = agent.check(LatLon(lat=21.9, lon=88.9))
    assert result.within_mpa is True


def test_point_near_imbl_boundary():
    result = agent.check(LatLon(lat=10.09, lon=80.06))
    assert result.within_imbl_buffer is True
    assert result.imbl_distance_km < 5


def test_open_water_trips_neither():
    result = agent.check(LatLon(lat=15.0, lon=70.0))
    assert result.within_mpa is False
    assert result.within_imbl_buffer is False


# --------------------------------------------------------------------------- #
# list_nearby — "which zones should be avoided" listing (issue #174)
# --------------------------------------------------------------------------- #
def test_list_nearby_includes_the_mpa_the_point_is_inside():
    zones = agent.list_nearby(LatLon(lat=21.9, lon=88.9))  # inside Sundarbans
    assert zones is not None
    containing = [m for m in zones.mpas if m.contains_point]
    assert containing, "expected the enclosing MPA in the nearby list"
    assert containing[0].distance_km == 0.0
    assert zones.mpas == sorted(zones.mpas, key=lambda m: m.distance_km)


def test_list_nearby_open_water_tight_radius_is_empty():
    zones = agent.list_nearby(LatLon(lat=15.0, lon=70.0), radius_km=1.0)
    assert zones is not None
    assert zones.mpas == []
    assert zones.radius_km == 1.0


def test_list_nearby_wide_radius_from_coast_finds_zones():
    # A generous radius off the West Bengal coast should surface at least the
    # Sundarbans cluster without the point being inside any of them.
    zones = agent.list_nearby(LatLon(lat=20.8, lon=89.2), radius_km=300.0)
    assert zones is not None
    assert len(zones.mpas) >= 1
    assert all(0.0 <= m.distance_km <= 300.0 for m in zones.mpas)


# --------------------------------------------------------------------- #
# #99 defect 1 regression: the IMBL geometry must be the negotiated
# boundary lines, not a polygon whose landward ring is the coastline.
# --------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("port", "lat", "lon"),
    [("Kochi", 9.93, 76.26), ("Chennai", 13.08, 80.27), ("Kollam", 8.88, 76.60)],
)
def test_coastal_ports_are_not_imbl_violations(port: str, lat: float, lon: float):
    """These are the three fishing ports the whole demo is built around. When
    the EEZ polygon was used as the IMBL proxy they all came back inside the
    5 km buffer (0.8-2.1 km) because `geom.boundary` includes the coastal
    ring — and FR-GEO-4 turns that into an un-overridable UNSAFE."""
    result = agent.check(LatLon(lat=lat, lon=lon))
    assert result.within_imbl_buffer is False, (
        f"{port} flagged as an IMBL violation at {result.imbl_distance_km:.2f} km — "
        "the shoreline is being measured as a maritime boundary again (#99)"
    )


def test_point_on_the_india_sri_lanka_imbl_is_still_flagged():
    """The other half of the same regression: fixing the false positives must
    not cost the true positive. Katchatheevu sits on the India-Sri Lanka
    maritime boundary — the line Indian fishermen are actually detained for
    crossing, so this is the case FR-GEO-2 exists for."""
    result = agent.check(LatLon(lat=9.38, lon=79.52))
    assert result.within_imbl_buffer is True
    assert result.imbl_distance_km < 5


# --------------------------------------------------------------------- #
# #99 defect 2 regression: unavailable must not be reported as "clear".
# --------------------------------------------------------------------- #
def test_unreadable_boundary_data_returns_none_not_a_clear_result(monkeypatch, tmp_path):
    """A geofence that could not be checked is not a geofence that was
    cleared. Returning a GeofenceResult with both flags False here would be a
    fabricated all-clear, and Figure 2 would answer SAFE with no boundary data
    at all (NFR-REL-2, FR-RISK-3)."""
    from app.core.config import get_settings

    monkeypatch.setattr(
        get_settings(), "gis_boundary_data_path", str(tmp_path / "missing.geojson")
    )

    result = GeofencingAgent(GISBoundaryAdapter()).check(LatLon(lat=9.93, lon=76.26))

    assert result is None


def test_unreadable_boundary_data_forces_insufficient_data_not_safe(monkeypatch, tmp_path):
    """The end-to-end consequence, asserted through the real Figure 2 tree so
    this cannot regress by someone "fixing" the sentinel in isolation."""
    from datetime import UTC, datetime

    from app.agents.risk_safety_agent import RiskSafetyAgent
    from app.core.config import get_settings
    from app.schemas.weather import WeatherResult

    monkeypatch.setattr(
        get_settings(), "gis_boundary_data_path", str(tmp_path / "missing.geojson")
    )
    geofence = GeofencingAgent(GISBoundaryAdapter()).check(LatLon(lat=9.93, lon=76.26))

    # Perfect weather, so nothing but the missing geofence can drive the verdict.
    calm = WeatherResult(
        wind_speed_kmh=5.0,
        wave_height_m=0.3,
        active_alerts=[],
        data_timestamp=datetime.now(UTC),
        status="ok",
        alerts_source_available=True,
    )

    verdict = RiskSafetyAgent().evaluate(calm, geofence, None)

    assert verdict.verdict == "INSUFFICIENT_DATA"

