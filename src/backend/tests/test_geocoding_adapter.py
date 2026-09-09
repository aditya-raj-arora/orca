"""
GeocodingAdapter unit tests (#110, FR-PLAN-1, NFR-REL-1, LLD §2.9).

The country-bias tests are the important ones: without them "Kochi" silently
resolves to Kochi, JAPAN and every downstream agent answers confidently about
the wrong hemisphere. Wrong-location data is fabricated data (NFR-REL-1); it
just arrives via the location rather than the value.

Owner: P1. `httpx.get` is stubbed so the real fetch/parse path runs offline.
"""
from __future__ import annotations

import httpx
import pytest

from app.data_access import geocoding_adapter as ga
from app.data_access.geocoding_adapter import GeocodingAdapter, _pick

# Trimmed real responses from geocoding-api.open-meteo.com.
KOCHI_JP = {
    "name": "Kochi", "country_code": "JP", "latitude": 33.55, "longitude": 133.533,
    "admin1": "Kochi", "population": 332059,
}
KOCHI_IN = {
    "name": "Kochi", "country_code": "IN", "latitude": 9.93988, "longitude": 76.26022,
    "admin1": "Kerala", "population": 633553,
}
CHENNAI_IN = {
    "name": "Chennai", "country_code": "IN", "latitude": 13.08784, "longitude": 80.27847,
    "admin1": "Tamil Nadu", "population": 4681087,
}


def _stub(monkeypatch: pytest.MonkeyPatch, results, *, status: int = 200, calls=None):
    def _get(url, **kw):
        if calls is not None:
            calls.append(kw.get("params", {}))
        return httpx.Response(
            status, json={"results": results}, request=httpx.Request("GET", str(url))
        )

    monkeypatch.setattr(httpx, "get", _get)


@pytest.fixture()
def geo() -> GeocodingAdapter:
    return GeocodingAdapter()


# --------------------------------------------------------------------------- #
# Country bias — the reason this adapter has a docstring that long
# --------------------------------------------------------------------------- #
def test_kochi_resolves_to_india_not_japan(
    geo: GeocodingAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Japan is genuinely first in the provider's global population ranking.
    _stub(monkeypatch, [KOCHI_JP, KOCHI_IN])
    res = geo.fetch({"place_name": "Kochi"})

    assert res.status == "ok"
    assert res.data["country_code"] == "IN"
    assert res.data["lat"] == pytest.approx(9.93988)
    assert res.data["lon"] == pytest.approx(76.26022)


def test_country_code_is_sent_to_the_provider(
    geo: GeocodingAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict] = []
    _stub(monkeypatch, [CHENNAI_IN], calls=calls)
    geo.fetch({"place_name": "Chennai"})
    assert calls[0]["countryCode"] == "IN"


def test_wrong_country_only_result_is_unavailable_not_a_silent_wrong_answer(
    geo: GeocodingAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Belt-and-braces: if the provider ever ignores countryCode, a foreign hit
    # must not win. "I don't know" beats coordinates in the wrong country.
    _stub(monkeypatch, [KOCHI_JP])
    res = geo.fetch({"place_name": "Kochi"})
    assert res.status == "unavailable"
    assert res.data is None


def test_pick_returns_a_foreign_hit_only_when_searching_globally() -> None:
    assert _pick([KOCHI_JP, KOCHI_IN], "IN")["country_code"] == "IN"
    assert _pick([KOCHI_JP], "IN") is None
    assert _pick([KOCHI_JP], "")["country_code"] == "JP"  # bias explicitly disabled


def test_pick_skips_candidates_with_unusable_coordinates() -> None:
    broken = {"name": "Nowhere", "country_code": "IN", "latitude": None, "longitude": 76.0}
    assert _pick([broken, KOCHI_IN], "IN")["lat"] == pytest.approx(9.93988)
    assert _pick([broken], "IN") is None


# --------------------------------------------------------------------------- #
# LLD §2.9 contract: never raise, never fabricate
# --------------------------------------------------------------------------- #
def test_unknown_place_is_unavailable(
    geo: GeocodingAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(monkeypatch, [])
    res = geo.fetch({"place_name": "asdfqwerzz"})
    assert res.status == "unavailable"
    assert res.data is None


@pytest.mark.parametrize(
    "behaviour",
    [
        lambda url, **kw: (_ for _ in ()).throw(httpx.ConnectError("stub: refused")),
        lambda url, **kw: (_ for _ in ()).throw(httpx.ReadTimeout("stub: timeout")),
        lambda url, **kw: httpx.Response(500, json={}, request=httpx.Request("GET", str(url))),
        lambda url, **kw: httpx.Response(
            200, text="<html>", request=httpx.Request("GET", str(url))
        ),
    ],
    ids=["connect", "timeout", "500", "non-json"],
)
def test_transport_failures_are_unavailable_never_raise(
    geo: GeocodingAdapter, monkeypatch: pytest.MonkeyPatch, behaviour
) -> None:
    monkeypatch.setattr(httpx, "get", behaviour)
    res = geo.fetch({"place_name": "Kochi"})
    assert res.status == "unavailable"
    assert res.data is None


def test_bad_params_never_raise(geo: GeocodingAdapter) -> None:
    for bad in ({}, {"place_name": None}, {"place_name": ""}, {"place_name": "   "}):
        assert geo.fetch(bad).status == "unavailable"


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #
def test_successful_lookup_is_cached(
    geo: GeocodingAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict] = []
    _stub(monkeypatch, [CHENNAI_IN], calls=calls)
    geo.fetch({"place_name": "Chennai"})
    geo.fetch({"place_name": "  chennai  "})  # normalised to the same key
    assert len(calls) == 1


def test_failed_lookup_is_not_cached(
    geo: GeocodingAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A transport blip must not poison the name for the life of the process.
    monkeypatch.setattr(
        httpx, "get", lambda url, **kw: (_ for _ in ()).throw(httpx.ConnectError("stub"))
    )
    assert geo.fetch({"place_name": "Chennai"}).status == "unavailable"

    _stub(monkeypatch, [CHENNAI_IN])
    assert geo.fetch({"place_name": "Chennai"}).status == "ok"


def test_cached_data_is_copied_not_shared(
    geo: GeocodingAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A caller mutating a returned dict must not corrupt the cache entry.
    _stub(monkeypatch, [CHENNAI_IN])
    first = geo.fetch({"place_name": "Chennai"}).data
    first["lat"] = 0.0
    assert geo.fetch({"place_name": "Chennai"}).data["lat"] == pytest.approx(13.08784)


def test_reset_cache_hook_clears_entries(
    geo: GeocodingAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict] = []
    _stub(monkeypatch, [CHENNAI_IN], calls=calls)
    geo.fetch({"place_name": "Chennai"})
    ga._reset_cache()
    geo.fetch({"place_name": "Chennai"})
    assert len(calls) == 2


# --------------------------------------------------------------------------- #
# Rate-limit handling (#116)
# --------------------------------------------------------------------------- #
# GeocodingAdapter is a third Open-Meteo endpoint on the same metered client IP
# as forecast/marine. Before #116 it called httpx.get directly: no retry, no
# Retry-After, no cooldown — so a 429 here was answered by calling straight
# back on the next query. These pin that it now follows the same policy as the
# weather sources (data_access/http_client.py).
def test_429_puts_geocoding_in_cooldown_instead_of_calling_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.data_access import http_client

    calls = {"n": 0}

    def _429(url, **_kw):
        calls["n"] += 1
        return httpx.Response(
            429,
            headers={"Retry-After": "60"},
            request=httpx.Request("GET", str(url)),
        )

    monkeypatch.setattr(httpx, "get", _429)
    adapter = GeocodingAdapter()

    assert adapter.fetch({"place_name": "Kochi"}).status == "unavailable"
    assert adapter.fetch({"place_name": "Chennai"}).status == "unavailable"

    assert calls["n"] == 1  # second lookup never left the process
    assert http_client.cooldown_remaining_s("open-meteo/geocoding") > 0


def test_geocoding_429_does_not_cool_down_the_weather_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Open-Meteo meters the geocoding API separately from forecast/marine, so
    # sharing the mechanism must not mean sharing the penalty — a place-name
    # lookup failing is not a reason to stop asking for wind.
    from app.data_access import http_client

    monkeypatch.setattr(
        httpx,
        "get",
        lambda url, **_kw: httpx.Response(429, request=httpx.Request("GET", str(url))),
    )
    GeocodingAdapter().fetch({"place_name": "Kochi"})

    assert http_client.cooldown_remaining_s("open-meteo/geocoding") > 0
    assert http_client.cooldown_remaining_s("open-meteo/forecast") == 0
