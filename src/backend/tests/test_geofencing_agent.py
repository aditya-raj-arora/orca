from app.agents.geofencing_agent import GeofencingAgent
from app.data_access.gis_boundary_adapter import GISBoundaryAdapter
from app.schemas.common import LatLon

agent = GeofencingAgent(GISBoundaryAdapter())

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