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