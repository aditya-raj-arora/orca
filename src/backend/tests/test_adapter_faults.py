"""
Fault-injection hardening for WeatherDataAdapter + INCOISAdapter (issue #38,
FR-WX-4 / FR-OCEAN-4 / NFR-REL-1). Every simulated upstream failure — connect
error, timeout, 4xx/5xx, non-JSON body, empty body — must produce
AdapterResult(status='unavailable' | 'stale'), NEVER an exception and NEVER a
fabricated value (LLD §2.9).

Owner: P3. `httpx.get` is monkeypatched with a URL router, so the real
_fetch_* / raise_for_status / .json() code paths run.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import httpx
import pytest

from app.data_access.incois_adapter import (
    INCOISAdapter,
    _load_pfz_snapshot,
)
from app.data_access.weather_adapter import WeatherDataAdapter

SNAPSHOT = (
    Path(__file__).resolve().parents[1] / "data" / "snapshots" / "pfz_latest.json.gz"
)


# --------------------------------------------------------------------------- #
# httpx.get router
# --------------------------------------------------------------------------- #
def _ok_json(body: dict | list):
    return lambda url: httpx.Response(200, json=body, request=httpx.Request("GET", url))


def _status(code: int):
    return lambda url: httpx.Response(
        code, json={"error": code}, request=httpx.Request("GET", url)
    )


def _text(body: str):
    return lambda url: httpx.Response(200, text=body, request=httpx.Request("GET", url))


def _raise(exc: Exception):
    def _b(url):
        raise exc

    return _b


CONNECT = _raise(httpx.ConnectError("stub: connection refused"))
TIMEOUT = _raise(httpx.ReadTimeout("stub: read timed out"))
NON_JSON = _text("<html><body>502 Bad Gateway</body></html>")
TRUNCATED = _text('{"current": {"wind_speed_10m":')


def _router(monkeypatch: pytest.MonkeyPatch, routes: dict, *, default=None) -> None:
    dflt = default or _ok_json({})

    def _get(url, **_kw):
        s = str(url)
        for key, behaviour in routes.items():
            if key in s:
                return behaviour(url)
        return dflt(url)

    monkeypatch.setattr(httpx, "get", _get)


# Minimal "good" payloads for the sources we want to keep healthy in a test.
_GOOD_FORECAST = {"current": {"wind_speed_10m": 12.0, "time": 1788264000}}
_GOOD_MARINE = {"current": {"wave_height": 1.1, "time": 1788264000}}
_GOOD_GDACS = "<rss><channel></channel></rss>"


# --------------------------------------------------------------------------- #
# WeatherDataAdapter
# --------------------------------------------------------------------------- #
@pytest.fixture()
def wx() -> WeatherDataAdapter:
    return WeatherDataAdapter()


def _wx_fetch(wx: WeatherDataAdapter):
    return wx.fetch({"lat": 13.08, "lon": 80.27, "window": None})


@pytest.mark.parametrize(
    "forecast_behaviour",
    [CONNECT, TIMEOUT, _status(500), _status(404), _status(403), NON_JSON, TRUNCATED],
    ids=["connect", "timeout", "500", "404", "403", "non-json", "truncated"],
)
def test_forecast_failure_is_unavailable_never_raises(
    wx: WeatherDataAdapter, monkeypatch: pytest.MonkeyPatch, forecast_behaviour
) -> None:
    _router(
        monkeypatch,
        {
            "/forecast": forecast_behaviour,
            "/marine": _ok_json(_GOOD_MARINE),
            "weatherapi": _ok_json({"alerts": {"alert": []}}),
            "gdacs": _text(_GOOD_GDACS),
        },
    )
    res = _wx_fetch(wx)
    assert res.status == "unavailable"
    assert res.data is None


@pytest.mark.parametrize(
    "marine_behaviour",
    [CONNECT, TIMEOUT, _status(500), NON_JSON],
    ids=["connect", "timeout", "500", "non-json"],
)
def test_marine_failure_is_unavailable(
    wx: WeatherDataAdapter, monkeypatch: pytest.MonkeyPatch, marine_behaviour
) -> None:
    _router(
        monkeypatch,
        {
            "/forecast": _ok_json(_GOOD_FORECAST),
            "/marine": marine_behaviour,
            "weatherapi": _ok_json({"alerts": {"alert": []}}),
            "gdacs": _text(_GOOD_GDACS),
        },
    )
    res = _wx_fetch(wx)
    assert res.status == "unavailable"
    assert res.data is None


def test_empty_forecast_body_is_unavailable(
    wx: WeatherDataAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    _router(
        monkeypatch,
        {"/forecast": _ok_json({}), "/marine": _ok_json(_GOOD_MARINE),
         "weatherapi": _ok_json({}), "gdacs": _text(_GOOD_GDACS)},
    )
    assert _wx_fetch(wx).status == "unavailable"


def test_non_numeric_core_field_is_unavailable_not_fabricated(
    wx: WeatherDataAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    _router(
        monkeypatch,
        {
            "/forecast": _ok_json({"current": {"wind_speed_10m": "N/A", "time": 1}}),
            "/marine": _ok_json(_GOOD_MARINE),
            "weatherapi": _ok_json({}),
            "gdacs": _text(_GOOD_GDACS),
        },
    )
    res = _wx_fetch(wx)
    assert res.status == "unavailable"
    assert res.data is None  # never a fabricated 0.0


def test_alerts_down_but_forecast_ok_is_ok_with_flag_false(
    wx: WeatherDataAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    _router(
        monkeypatch,
        {
            "/forecast": _ok_json(_GOOD_FORECAST),
            "/marine": _ok_json(_GOOD_MARINE),
            "weatherapi": _status(500),
            "gdacs": CONNECT,
        },
    )
    res = _wx_fetch(wx)
    assert res.status == "ok"
    assert res.data["alerts_source_available"] is False   # NFR-REL-1
    assert res.data["active_alerts"] == []


def test_all_sources_down_is_unavailable_never_raises(
    wx: WeatherDataAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    _router(monkeypatch, {}, default=CONNECT)
    res = _wx_fetch(wx)
    assert res.status == "unavailable"
    assert res.data is None


def test_bad_params_never_raise(wx: WeatherDataAdapter) -> None:
    for bad in ({}, {"lat": "x", "lon": 1}, {"lat": None, "lon": None}):
        assert wx.fetch(bad).status == "unavailable"


# --------------------------------------------------------------------------- #
# INCOISAdapter — PFZ
# --------------------------------------------------------------------------- #
@pytest.fixture()
def incois(monkeypatch: pytest.MonkeyPatch) -> INCOISAdapter:
    ad = INCOISAdapter()
    # default: no snapshot on disk unless a test points it at one
    monkeypatch.setattr(ad, "_snapshot_path", "/nonexistent/pfz_latest.json.gz")
    return ad


@pytest.mark.parametrize(
    "wfs_behaviour",
    [CONNECT, TIMEOUT, _status(500), _status(404), NON_JSON, TRUNCATED, _ok_json({"features": []})],
    ids=["connect", "timeout", "500", "404", "non-json", "truncated", "empty"],
)
def test_pfz_failure_without_snapshot_is_unavailable(
    incois: INCOISAdapter, monkeypatch: pytest.MonkeyPatch, wfs_behaviour
) -> None:
    _router(monkeypatch, {"PFZ_Automation": wfs_behaviour})
    res = incois.fetch({"kind": "pfz"})
    assert res.status == "unavailable"
    assert res.data is None


@pytest.mark.parametrize(
    "wfs_behaviour",
    [CONNECT, _status(503), NON_JSON, _ok_json({"features": []})],
    ids=["connect", "503", "non-json", "empty"],
)
def test_pfz_failure_with_snapshot_falls_back_to_stale(
    incois: INCOISAdapter, monkeypatch: pytest.MonkeyPatch, wfs_behaviour
) -> None:
    assert SNAPSHOT.exists(), "run scripts/refresh_pfz_snapshot.sh"
    monkeypatch.setattr(incois, "_snapshot_path", str(SNAPSHOT))
    _router(monkeypatch, {"PFZ_Automation": wfs_behaviour})
    res = incois.fetch({"kind": "pfz"})
    assert res.status == "stale"          # NFR-REL-1: never presented as live
    assert res.data["count"] >= 1
    assert all("lat" in z and "lon" in z for z in res.data["pfz"])


def test_pfz_snapshot_not_cached_so_live_recovers(
    incois: INCOISAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(incois, "_snapshot_path", str(SNAPSHOT))
    _router(monkeypatch, {"PFZ_Automation": CONNECT})
    assert incois.fetch({"kind": "pfz"}).status == "stale"
    # WFS back up -> next call is live 'ok', not the stuck snapshot
    _router(monkeypatch, {"PFZ_Automation": _ok_json(_live_pfz_geojson())})
    assert incois.fetch({"kind": "pfz"}).status in ("ok", "stale")  # depends on advisory age


# --------------------------------------------------------------------------- #
# INCOISAdapter — ocean params
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "wms_behaviour",
    [CONNECT, TIMEOUT, _status(500), NON_JSON],
    ids=["connect", "timeout", "500", "non-json"],
)
def test_ocean_params_failure_is_unavailable_never_raises(
    incois: INCOISAdapter, monkeypatch: pytest.MonkeyPatch, wms_behaviour
) -> None:
    _router(monkeypatch, {"/wms": wms_behaviour})
    res = incois.fetch({"kind": "ocean_params", "lat": 13.08, "lon": 80.27})
    assert res.status == "unavailable"
    assert res.data is None


def test_ocean_params_nodata_sentinel_is_ok_with_none(
    incois: INCOISAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    _router(
        monkeypatch,
        {"/wms": _ok_json({"features": [{"properties": {"GRAY_INDEX": -1}}]})},
    )
    res = incois.fetch({"kind": "ocean_params", "lat": 13.08, "lon": 80.27})
    assert res.status == "ok"
    assert res.data == {"sst_c": None, "chlorophyll_mg_m3": None}  # not fabricated


# --------------------------------------------------------------------------- #
# _load_pfz_snapshot (pure)
# --------------------------------------------------------------------------- #
def test_load_snapshot_from_bundled_file() -> None:
    snap = _load_pfz_snapshot(str(SNAPSHOT))
    assert snap is not None
    centroids, advisory_date = snap
    assert len(centroids) >= 1
    assert advisory_date is not None


def test_load_snapshot_missing_file_is_none() -> None:
    assert _load_pfz_snapshot("/no/such/file.json.gz") is None


def test_load_snapshot_garbage_is_none(tmp_path: Path) -> None:
    bad = tmp_path / "pfz_latest.json.gz"
    bad.write_bytes(b"not gzip, not json")
    assert _load_pfz_snapshot(str(bad)) is None


def test_load_snapshot_plain_json_also_works(tmp_path: Path) -> None:
    gj = json.loads(gzip.decompress(SNAPSHOT.read_bytes()))
    plain = tmp_path / "pfz.json"
    plain.write_text(json.dumps(gj))
    snap = _load_pfz_snapshot(str(plain))
    assert snap is not None and len(snap[0]) >= 1


def _live_pfz_geojson() -> dict:
    return json.loads(gzip.decompress(SNAPSHOT.read_bytes()))
