"""
Unit tests for OceanAgent, haversine_km + INCOISAdapter helpers
(issue #15, FR-OCEAN-1..4).

Owner: P3. No network: the agent uses a fake adapter; the adapter's pure
helpers run against the captured PFZ GeoJSON sample; fetch() orchestration is
tested with monkeypatched HTTP methods.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.agents.ocean_agent import OceanAgent, bearing_deg, haversine_km
from app.data_access.base import AdapterResult
from app.data_access.incois_adapter import (
    INCOISAdapter,
    _advisory_date_from_props,
    _gray_index_value,
    _parse_pfz_geojson,
)
from app.schemas.common import LatLon

SAMPLE_PFZ = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "samples"
    / "incois"
    / "pfz_wfs_pfzlines_sample.json"
)


# --------------------------------------------------------------------------- #
# haversine_km — the LLD §4.3 formula (issue #15: "matches exactly")
# --------------------------------------------------------------------------- #
def _lld_haversine(lat1, lon1, lat2, lon2):  # noqa: ANN001 - local reference impl
    from math import asin, cos, radians, sin, sqrt

    R = 6371.0
    phi1, phi2 = radians(lat1), radians(lat2)
    d_phi = radians(lat2 - lat1)
    d_lambda = radians(lon2 - lon1)
    a = sin(d_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(d_lambda / 2) ** 2
    return 2 * R * asin(sqrt(a))


@pytest.mark.parametrize(
    "a,b",
    [
        ((9.93, 76.26), (9.93, 76.26)),
        ((0.0, 0.0), (0.0, 1.0)),
        ((0.0, 0.0), (1.0, 0.0)),
        ((13.08, 80.27), (9.93, 76.26)),
        ((51.5074, -0.1278), (48.8566, 2.3522)),
        ((-33.87, 151.21), (40.71, -74.01)),
    ],
)
def test_haversine_matches_lld_formula_exactly(a, b) -> None:
    assert haversine_km(a[0], a[1], b[0], b[1]) == _lld_haversine(a[0], a[1], b[0], b[1])


def test_haversine_zero_for_same_point() -> None:
    assert haversine_km(8.88, 76.60, 8.88, 76.60) == 0.0


def test_haversine_known_distances() -> None:
    # one degree of longitude / latitude at the equator ~ 111.19 km
    assert haversine_km(0, 0, 0, 1) == pytest.approx(111.19, abs=0.1)
    assert haversine_km(0, 0, 1, 0) == pytest.approx(111.19, abs=0.1)
    # London -> Paris ~ 343 km; Sydney -> New York ~ 15990 km
    assert haversine_km(51.5074, -0.1278, 48.8566, 2.3522) == pytest.approx(343, abs=3)
    assert haversine_km(-33.87, 151.21, 40.71, -74.01) == pytest.approx(15990, abs=50)


def test_haversine_symmetric() -> None:
    d1 = haversine_km(9.93, 76.26, 13.08, 80.27)
    d2 = haversine_km(13.08, 80.27, 9.93, 76.26)
    assert d1 == d2


def test_bearing_cardinal_directions() -> None:
    assert bearing_deg(0, 0, 1, 0) == pytest.approx(0.0, abs=1e-6)  # due north
    assert bearing_deg(0, 0, 0, 1) == pytest.approx(90.0, abs=1e-6)  # due east
    assert bearing_deg(0, 0, -1, 0) == pytest.approx(180.0, abs=1e-6)  # due south
    assert 0.0 <= bearing_deg(9.93, 76.26, 13.08, 80.27) < 360.0


# --------------------------------------------------------------------------- #
# INCOISAdapter pure helpers
# --------------------------------------------------------------------------- #
def test_gray_index_no_data_sentinels_map_to_none() -> None:
    assert _gray_index_value(None) is None
    assert _gray_index_value(-1) is None
    assert _gray_index_value(-999.0) is None
    assert _gray_index_value("not-a-number") is None
    assert _gray_index_value(29.68) == 29.68
    assert _gray_index_value("30.1") == 30.1
    assert _gray_index_value(0.0) == 0.0


def test_advisory_date_from_year_and_julian_day() -> None:
    assert _advisory_date_from_props({"Year": 2026, "Julian_day": "243"}) == datetime(
        2026, 8, 31, tzinfo=UTC
    )
    assert _advisory_date_from_props({"Year": 2026, "Julian_day": "1"}) == datetime(
        2026, 1, 1, tzinfo=UTC
    )
    assert _advisory_date_from_props({"Year": 2026}) is None
    assert _advisory_date_from_props({"Year": "x", "Julian_day": "10"}) is None
    assert _advisory_date_from_props({"Year": 2026, "Julian_day": "999"}) is None


def test_parse_pfz_geojson_from_captured_sample() -> None:
    gj = json.loads(SAMPLE_PFZ.read_text())
    centroids, advisory_date = _parse_pfz_geojson(gj)
    assert len(centroids) >= 2
    for c in centroids:
        assert 5.0 < c["lat"] < 25.0  # Indian coastal latitudes
        assert 68.0 < c["lon"] < 90.0
        assert c["uid"] is not None
    assert advisory_date == datetime(2026, 8, 31, tzinfo=UTC)


def test_parse_pfz_geojson_skips_features_without_coords() -> None:
    gj = {
        "features": [
            {
                "geometry": {
                    "type": "MultiLineString",
                    "coordinates": [[[80.0, 13.0], [80.1, 13.1]]],
                },
                "properties": {"UID": 1, "Year": 2026, "Julian_day": "243"},
            },
            {"geometry": {"type": "MultiLineString", "_vertices": 200}, "properties": {"UID": 2}},
        ]
    }
    centroids, _ = _parse_pfz_geojson(gj)
    assert [c["uid"] for c in centroids] == [1]


def test_parse_pfz_geojson_empty() -> None:
    assert _parse_pfz_geojson({"features": []}) == ([], None)


# --------------------------------------------------------------------------- #
# OceanAgent.get_nearest_pfz — against a fake adapter
# --------------------------------------------------------------------------- #
class _FakeAdapter:
    def __init__(self, result: AdapterResult) -> None:
        self.result = result
        self.calls: list[dict] = []

    def fetch(self, params: dict) -> AdapterResult:
        self.calls.append(params)
        return self.result


_PFZ_POINTS = [
    {"lat": 15.0931, "lon": 73.4411, "uid": 1, "category": "ghrsst"},  # off Goa
    {"lat": 16.4155, "lon": 82.4210, "uid": 2, "category": "sst"},  # off Andhra
    {"lat": 13.4287, "lon": 80.6392, "uid": 3, "category": "sst"},  # off Chennai
]


def _pfz_result(status: str = "ok", advisory: str | None = "2026-08-31T00:00:00+00:00"):
    return AdapterResult(
        data={"pfz": _PFZ_POINTS, "advisory_date": advisory, "count": 3},
        fetched_at=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        status=status,
    )


def test_nearest_pfz_picks_minimum_distance_zone() -> None:
    agent = OceanAgent(_FakeAdapter(_pfz_result()))
    r = agent.get_nearest_pfz(LatLon(13.08, 80.27))  # near the Chennai line
    assert r is not None
    assert (r.centroid.lat, r.centroid.lon) == (13.4287, 80.6392)
    assert r.distance_km == pytest.approx(haversine_km(13.08, 80.27, 13.4287, 80.6392), abs=0.01)
    assert 0.0 <= r.bearing_deg < 360.0


@pytest.mark.parametrize(
    "loc,expected_uid",
    [
        (LatLon(15.30, 73.80), 1),  # Goa
        (LatLon(16.50, 82.20), 2),  # Andhra
        (LatLon(13.00, 80.30), 3),  # Chennai
    ],
)
def test_nearest_pfz_for_three_locations(loc: LatLon, expected_uid: int) -> None:
    agent = OceanAgent(_FakeAdapter(_pfz_result()))
    r = agent.get_nearest_pfz(loc)
    assert r is not None
    got = min(_PFZ_POINTS, key=lambda p: haversine_km(loc.lat, loc.lon, p["lat"], p["lon"]))
    assert got["uid"] == expected_uid
    assert (r.centroid.lat, r.centroid.lon) == (got["lat"], got["lon"])


def test_nearest_pfz_none_when_adapter_unavailable() -> None:
    r = OceanAgent(_FakeAdapter(AdapterResult(None, datetime.now(UTC), "unavailable")))
    assert r.get_nearest_pfz(LatLon(9.93, 76.26)) is None


def test_nearest_pfz_none_when_no_features() -> None:
    empty = AdapterResult({"pfz": [], "advisory_date": None}, datetime.now(UTC), "ok")
    assert OceanAgent(_FakeAdapter(empty)).get_nearest_pfz(LatLon(9.93, 76.26)) is None


def test_is_stale_true_when_adapter_reports_stale() -> None:
    agent = OceanAgent(_FakeAdapter(_pfz_result(status="stale")))
    assert agent.get_nearest_pfz(LatLon(13.08, 80.27)).is_stale is True


def test_is_stale_false_for_fresh_advisory() -> None:
    fresh = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    agent = OceanAgent(_FakeAdapter(_pfz_result(advisory=fresh)))
    assert agent.get_nearest_pfz(LatLon(13.08, 80.27)).is_stale is False


def test_is_stale_true_when_advisory_older_than_threshold() -> None:
    old = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    agent = OceanAgent(_FakeAdapter(_pfz_result(advisory=old)))
    assert agent.get_nearest_pfz(LatLon(13.08, 80.27)).is_stale is True


def test_data_timestamp_falls_back_to_fetched_at_when_no_advisory_date() -> None:
    res = _pfz_result(advisory=None)
    agent = OceanAgent(_FakeAdapter(res))
    r = agent.get_nearest_pfz(LatLon(13.08, 80.27))
    assert r.data_timestamp == res.fetched_at


# --------------------------------------------------------------------------- #
# OceanAgent.get_ocean_parameters — no fabrication (FR-OCEAN-2)
# --------------------------------------------------------------------------- #
def _params_result(sst, chl, status="ok"):
    return AdapterResult(
        {"sst_c": sst, "chlorophyll_mg_m3": chl}, datetime(2026, 9, 1, tzinfo=UTC), status
    )


def test_ocean_parameters_surfaces_real_values() -> None:
    agent = OceanAgent(_FakeAdapter(_params_result(29.68, 0.144)))
    p = agent.get_ocean_parameters(LatLon(9.93, 76.26))
    assert p.sea_surface_temp_c == 29.68
    assert p.chlorophyll_mg_m3 == 0.144
    assert p.data_timestamp is not None


def test_ocean_parameters_none_for_unpublished_fields() -> None:
    # adapter reached INCOIS, but no data published at this point -> None, not 0
    agent = OceanAgent(_FakeAdapter(_params_result(None, None)))
    p = agent.get_ocean_parameters(LatLon(9.93, 76.26))
    assert p.sea_surface_temp_c is None
    assert p.chlorophyll_mg_m3 is None
    assert p.data_timestamp is None


def test_ocean_parameters_partial_publication() -> None:
    agent = OceanAgent(_FakeAdapter(_params_result(30.1, None)))
    p = agent.get_ocean_parameters(LatLon(8.88, 76.60))
    assert p.sea_surface_temp_c == 30.1
    assert p.chlorophyll_mg_m3 is None
    assert p.data_timestamp is not None


def test_ocean_parameters_unavailable_is_all_none() -> None:
    agent = OceanAgent(_FakeAdapter(_params_result(None, None, status="unavailable")))
    p = agent.get_ocean_parameters(LatLon(9.93, 76.26))
    assert p.sea_surface_temp_c is None
    assert p.chlorophyll_mg_m3 is None
    assert p.data_timestamp is None


# --------------------------------------------------------------------------- #
# INCOISAdapter.fetch orchestration — monkeypatched HTTP
# --------------------------------------------------------------------------- #
@pytest.fixture()
def adapter() -> INCOISAdapter:
    return INCOISAdapter()


def test_fetch_pfz_ok_from_sample(adapter: INCOISAdapter, monkeypatch: pytest.MonkeyPatch) -> None:
    gj = json.loads(SAMPLE_PFZ.read_text())
    monkeypatch.setattr(adapter, "_get_pfz_geojson", lambda: gj)
    res = adapter.fetch({"kind": "pfz"})
    assert res.status in ("ok", "stale")
    assert res.data["count"] >= 2
    assert res.data["advisory_date"].startswith("2026-08-31")


def test_fetch_pfz_marks_stale_when_advisory_old(
    adapter: INCOISAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_jd = 5  # early January, well past the 48h default threshold
    gj = {
        "features": [
            {
                "geometry": {
                    "type": "MultiLineString",
                    "coordinates": [[[80.0, 13.0], [80.2, 13.2]]],
                },
                "properties": {"UID": 1, "Year": 2026, "Julian_day": str(old_jd)},
            }
        ]
    }
    monkeypatch.setattr(adapter, "_get_pfz_geojson", lambda: gj)
    assert adapter.fetch({"kind": "pfz"}).status == "stale"


def test_fetch_pfz_unavailable_when_http_raises(
    adapter: INCOISAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom() -> dict:
        raise RuntimeError("INCOIS down")

    monkeypatch.setattr(adapter, "_get_pfz_geojson", boom)
    res = adapter.fetch({"kind": "pfz"})
    assert res.status == "unavailable"
    assert res.data is None


def test_fetch_pfz_unavailable_when_no_parseable_features(
    adapter: INCOISAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(adapter, "_get_pfz_geojson", lambda: {"features": []})
    assert adapter.fetch({"kind": "pfz"}).status == "unavailable"


def test_fetch_ocean_params_maps_gray_index(
    adapter: INCOISAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    values = {"sst": 29.6, "chl": -1}  # chl is a no-data sentinel
    monkeypatch.setattr(
        adapter, "_get_gray_index", lambda layer, la, lo: _gray_index_value(values[layer])
    )
    res = adapter.fetch({"kind": "ocean_params", "lat": 9.93, "lon": 76.26})
    assert res.status == "ok"
    assert res.data == {"sst_c": 29.6, "chlorophyll_mg_m3": None}


def test_fetch_ocean_params_unavailable_when_http_raises(
    adapter: INCOISAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(layer: str, la: float, lo: float) -> float:
        raise RuntimeError("WMS down")

    monkeypatch.setattr(adapter, "_get_gray_index", boom)
    res = adapter.fetch({"kind": "ocean_params", "lat": 9.93, "lon": 76.26})
    assert res.status == "unavailable"
    assert res.data is None


def test_fetch_unknown_kind_is_unavailable(adapter: INCOISAdapter) -> None:
    assert adapter.fetch({"kind": "bogus"}).status == "unavailable"


def test_agent_over_adapter_end_to_end_stubbed(monkeypatch: pytest.MonkeyPatch) -> None:
    ad = INCOISAdapter()
    gj = json.loads(SAMPLE_PFZ.read_text())
    monkeypatch.setattr(ad, "_get_pfz_geojson", lambda: gj)
    r = OceanAgent(ad).get_nearest_pfz(LatLon(13.0, 80.3))
    assert r is not None
    assert 5.0 < r.centroid.lat < 25.0
    assert r.distance_km >= 0.0
    assert isinstance(r.is_stale, bool)
    assert r.data_timestamp.tzinfo is not None
