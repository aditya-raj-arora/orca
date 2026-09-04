"""
Rate-limit hardening for WeatherDataAdapter (issue #106, FR-WX-1/3/4,
NFR-REL-1, NFR-PERF-2).

Production symptom: Open-Meteo's keyless tier answered the forecast call with
`429 Too Many Requests`, which the FR-WX-4 contract correctly turned into
status='unavailable' — i.e. no weather at all for that query. These tests pin
the mitigations (result cache, 429 cooldown, bounded retry, WeatherAPI
fallback, optional customer API key) AND pin the guarantees they must not
break: never a fabricated value, never longer than the graph's
AGENT_TIMEOUT_SECONDS budget.

#116 corrected one of #106's: the 429 retry is gone. Open-Meteo meters per
client IP, Render's free plan shares that IP across the node, and retrying into
an IP-level bucket only deepens the hole — see data_access/http_client.py.
Retries now cover 5xx and transport errors only.

Owner: P3. Same `httpx.get` router pattern as tests/test_adapter_faults.py, so
the real _fetch_* / retry / cache code paths run.
"""
from __future__ import annotations

import time

import httpx
import pytest

from app.data_access import weather_adapter as wa
from app.data_access.weather_adapter import WeatherDataAdapter

_GOOD_FORECAST = {"current": {"wind_speed_10m": 12.0, "time": 1788264000}}
_GOOD_MARINE = {"current": {"wave_height": 1.1, "time": 1788264000}}
_GOOD_GDACS = "<rss><channel></channel></rss>"

CHENNAI = {"lat": 13.0827, "lon": 80.2707, "window": None}
KOCHI = {"lat": 9.93, "lon": 76.26, "window": None}


class Router:
    """httpx.get stub that counts calls per source and can change behaviour
    partway through (so "429 then success" is expressible)."""

    def __init__(self, **behaviours) -> None:
        self.behaviours = behaviours  # URL substring -> callable(url) -> Response
        self.calls: dict[str, int] = {}

    def install(self, monkeypatch: pytest.MonkeyPatch) -> Router:
        monkeypatch.setattr(httpx, "get", self)
        return self

    def __call__(self, url, **_kw):
        s = str(url)
        for key, behaviour in self.behaviours.items():
            if key in s:
                self.calls[key] = self.calls.get(key, 0) + 1
                return behaviour(s)
        raise AssertionError(f"unrouted URL in test: {s}")


def _json(body):
    return lambda url: httpx.Response(200, json=body, request=httpx.Request("GET", url))


def _text(body: str):
    return lambda url: httpx.Response(200, text=body, request=httpx.Request("GET", url))


def _429(retry_after: str | None = None):
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return lambda url: httpx.Response(
        429, text="rate limited", headers=headers, request=httpx.Request("GET", url)
    )


def _404(url):
    return httpx.Response(404, request=httpx.Request("GET", url))


def _502(url):
    return httpx.Response(502, request=httpx.Request("GET", url))


def _then(*behaviours):
    """Return behaviours[0] on the first call, [1] on the second, ... then
    repeat the last one for every call after that."""
    box = {"n": 0}

    def _b(url):
        i = min(box["n"], len(behaviours) - 1)
        box["n"] += 1
        return behaviours[i](url)

    return _b


def _healthy(**overrides) -> Router:
    routes = {
        # NOTE: "/forecast" also substring-matches WeatherAPI's
        # /v1/forecast.json, so weatherapi is keyed on its host and listed
        # first — dict insertion order is match order.
        "api.weatherapi.com": _json({"alerts": {"alert": []}}),
        "gdacs": _text(_GOOD_GDACS),
        "/marine": _json(_GOOD_MARINE),
        "/forecast": _json(_GOOD_FORECAST),
    }
    routes.update(overrides)
    return Router(**routes)


@pytest.fixture()
def wx() -> WeatherDataAdapter:
    return WeatherDataAdapter()


# --------------------------------------------------------------------------- #
# 1. Result cache — the mitigation that actually keeps us under the quota
# --------------------------------------------------------------------------- #
def test_repeat_query_is_served_from_cache_without_a_second_upstream_call(
    wx: WeatherDataAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    router = _healthy().install(monkeypatch)

    first = wx.fetch(CHENNAI)
    second = wx.fetch(CHENNAI)

    assert first.status == "ok"
    assert second.status == "ok"
    assert router.calls["/forecast"] == 1  # the whole point: one upstream call
    assert router.calls["/marine"] == 1


def test_cache_hit_keeps_the_original_observation_time_not_the_hit_time(
    wx: WeatherDataAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    # FR-WX-3: a cache hit must not present older data as freshly observed.
    _healthy().install(monkeypatch)
    first = wx.fetch(CHENNAI)
    second = wx.fetch(CHENNAI)
    assert second.fetched_at == first.fetched_at
    assert second.data["data_time_epoch"] == first.data["data_time_epoch"]


def test_nearby_points_share_a_cell_but_distant_ones_do_not(
    wx: WeatherDataAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    router = _healthy().install(monkeypatch)
    wx.fetch(CHENNAI)
    wx.fetch({"lat": 13.085, "lon": 80.272, "window": None})  # ~300 m away
    assert router.calls["/forecast"] == 1
    wx.fetch(KOCHI)
    assert router.calls["/forecast"] == 2


def test_failures_are_never_cached(
    wx: WeatherDataAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    # NFR-REL-1: a transient outage must not stick for the whole TTL. The
    # forecast 429s on the first fetch, then recovers.
    _healthy(
        **{"/forecast": _then(_429(), _json(_GOOD_FORECAST))}
    ).install(monkeypatch)

    assert wx.fetch(CHENNAI).status == "unavailable"
    wa._reset_rate_limit_state()  # drop the cooldown; this test is about the cache
    assert wx.fetch(CHENNAI).status == "ok"


def test_ttl_zero_disables_the_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    router = _healthy().install(monkeypatch)
    wx = WeatherDataAdapter()
    monkeypatch.setattr(wx, "_cache_ttl_s", 0.0)
    wx.fetch(CHENNAI)
    wx.fetch(CHENNAI)
    assert router.calls["/forecast"] == 2


def test_expired_entry_is_refetched(
    wx: WeatherDataAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    router = _healthy().install(monkeypatch)
    wx.fetch(CHENNAI)
    # Age the entry past the TTL rather than sleeping for ten minutes.
    with wa._CACHE_LOCK:
        for k, (_stored_at, result) in list(wa._RESULT_CACHE.items()):
            wa._RESULT_CACHE[k] = (time.monotonic() - wx._cache_ttl_s - 1.0, result)
    wx.fetch(CHENNAI)
    assert router.calls["/forecast"] == 2


# --------------------------------------------------------------------------- #
# 2. Bounded retry — on congestion, NEVER on a quota
# --------------------------------------------------------------------------- #
def test_429_is_not_retried(
    wx: WeatherDataAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    # #116's correction to #106. Open-Meteo's 429 is an IP-level minute / hour
    # / day bucket; it cannot clear inside our sub-second backoff, so the extra
    # attempts only added load to an API that had just refused us — and on
    # Render's shared egress IP that load is part of what keeps the bucket
    # empty. Exactly one call, then out.
    router = _healthy(**{"/forecast": _429()}).install(monkeypatch)

    wx.fetch(CHENNAI)

    assert router.calls["/forecast"] == 1


def test_5xx_is_still_retried_and_recovers(
    wx: WeatherDataAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A 502 is congestion, not a quota — genuinely worth another attempt.
    # Dropping the 429 retry must not have dropped this one too.
    router = _healthy(**{"/forecast": _then(_502, _json(_GOOD_FORECAST))}).install(
        monkeypatch
    )

    res = wx.fetch(CHENNAI)
    assert res.status == "ok"
    assert res.data["wind_speed_kmh"] == 12.0
    assert router.calls["/forecast"] == 2


def test_persistent_429_is_unavailable_never_fabricated(
    wx: WeatherDataAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    # FR-WX-4 unchanged: refusing to retry still degrades honestly rather than
    # inventing a wind speed. (No WeatherAPI key here, so no fallback either.)
    _healthy(**{"/forecast": _429()}).install(monkeypatch)
    res = wx.fetch(CHENNAI)
    assert res.status == "unavailable"
    assert res.data is None


def test_retry_attempts_are_capped(
    wx: WeatherDataAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    router = _healthy(**{"/forecast": _502}).install(monkeypatch)
    wx.fetch(CHENNAI)
    assert router.calls["/forecast"] == wa.http_client.MAX_ATTEMPTS


def test_non_retryable_4xx_is_not_retried(
    wx: WeatherDataAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A 404 is a wrong URL, not congestion — retrying it only spends budget.
    router = _healthy(**{"/forecast": _404}).install(monkeypatch)
    assert wx.fetch(CHENNAI).status == "unavailable"
    assert router.calls["/forecast"] == 1


def test_retry_never_exceeds_the_agent_timeout_budget(
    wx: WeatherDataAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    # NFR-PERF-2: the graph gives a node AGENT_TIMEOUT_SECONDS, and the retry
    # loop must stay inside it however many attempts the upstream buys.
    from app.orchestration.graph import AGENT_TIMEOUT_SECONDS

    _healthy(**{"/forecast": _502}).install(monkeypatch)
    started = time.monotonic()
    res = wx.fetch(CHENNAI)
    elapsed = time.monotonic() - started

    assert res.status == "unavailable"
    assert elapsed < AGENT_TIMEOUT_SECONDS


# --------------------------------------------------------------------------- #
# 3. 429 cooldown — stop adding load to an API that is already refusing us
# --------------------------------------------------------------------------- #
def test_429_puts_that_source_in_cooldown(
    wx: WeatherDataAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    router = _healthy(**{"/forecast": _429(retry_after="60")}).install(monkeypatch)

    wx.fetch(CHENNAI)
    wx.fetch(KOCHI)  # a different cell, so no cache hit to hide the effect

    assert router.calls["/forecast"] == 1  # the second fetch never left the box
    assert wa._cooldown_remaining_s("open-meteo/forecast") > 0


def test_cooldown_is_per_source_and_leaves_healthy_sources_alone(
    wx: WeatherDataAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    # WeatherAPI is a best-effort alert source; rate-limiting it must not stop
    # the required forecast/marine calls (NFR-REL-1 degradation, not outage).
    monkeypatch.setattr(wx, "_weatherapi_key", "test-key")
    router = _healthy(**{"api.weatherapi.com": _429(retry_after="60")}).install(
        monkeypatch
    )

    res = wx.fetch(CHENNAI)
    assert res.status == "ok"
    assert res.data["alerts_source_available"] is True  # GDACS was still reachable
    assert wa._cooldown_remaining_s("weatherapi/alerts") > 0
    assert wa._cooldown_remaining_s("open-meteo/forecast") == 0
    assert router.calls["/forecast"] == 1


def test_cooldown_is_capped(
    wx: WeatherDataAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A garbled or hostile Retry-After must not park a source for the rest of
    # the demo.
    _healthy(**{"/forecast": _429(retry_after="999999")}).install(monkeypatch)
    wx.fetch(CHENNAI)
    assert wa._cooldown_remaining_s("open-meteo/forecast") <= wa.http_client.MAX_COOLDOWN_S


def test_unparseable_retry_after_falls_back_to_the_default_cooldown(
    wx: WeatherDataAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The HTTP-date form is deliberately unsupported (see _retry_after_s).
    _healthy(
        **{"/forecast": _429(retry_after="Wed, 21 Oct 2026 07:28:00 GMT")}
    ).install(monkeypatch)
    wx.fetch(CHENNAI)
    assert 0 < wa._cooldown_remaining_s("open-meteo/forecast") <= wa.http_client.DEFAULT_COOLDOWN_S


# --------------------------------------------------------------------------- #
# 4. Provider fallback (#116)
#
# Open-Meteo's forecast leg 429s PERSISTENTLY from Render's shared egress IP,
# so retry and cache (above) cannot help. The WeatherAPI payload already
# fetched for alerts carries the same current-conditions fields.
# --------------------------------------------------------------------------- #
_WAPI_CURRENT = {
    "current": {
        "wind_kph": 21.6,
        "gust_kph": 25.6,
        "wind_degree": 143,
        "precip_mm": 0.0,
        "vis_km": 10.0,
        "condition": {"text": "Overcast", "code": 1009},
        "last_updated_epoch": 1788258600,
    },
    "alerts": {"alert": []},
}


def _wapi_healthy(**overrides) -> Router:
    r = _healthy(**{"api.weatherapi.com": _json(_WAPI_CURRENT)})
    r.behaviours.update(overrides)
    return r


def test_forecast_429_falls_back_to_weatherapi_current(
    wx: WeatherDataAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wx, "_weatherapi_key", "test-key")
    _wapi_healthy(**{"/forecast": _429()}).install(monkeypatch)

    res = wx.fetch(CHENNAI)

    assert res.status == "ok"  # was 'unavailable' — the whole point
    assert res.data["wind_speed_kmh"] == 21.6
    assert res.data["wind_gust_kmh"] == 25.6
    assert res.data["wind_direction_deg"] == 143.0
    assert res.data["visibility_m"] == 10_000.0  # vis_km -> m
    assert res.data["data_time_epoch"] == 1788258600
    assert res.data["forecast_source"] == "weatherapi"
    # Wave height still comes from Open-Meteo marine, which was never blocked.
    assert res.data["wave_height_m"] == 1.1


def test_fallback_does_not_fabricate_a_wmo_weather_code(
    wx: WeatherDataAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    # WeatherAPI condition codes are not WMO codes; emitting 1009 as one would
    # be a number that silently means something else (NFR-REL-1).
    monkeypatch.setattr(wx, "_weatherapi_key", "test-key")
    _wapi_healthy(**{"/forecast": _429()}).install(monkeypatch)
    assert wx.fetch(CHENNAI).data["weather_code"] is None


def test_open_meteo_is_preferred_when_it_works(
    wx: WeatherDataAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wx, "_weatherapi_key", "test-key")
    _wapi_healthy().install(monkeypatch)
    res = wx.fetch(CHENNAI)
    assert res.data["forecast_source"] == "open-meteo"
    assert res.data["wind_speed_kmh"] == 12.0  # not WeatherAPI's 21.6


def test_no_weatherapi_key_means_no_fallback_still_unavailable(
    wx: WeatherDataAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The deployed state before the key is set: fallback inert, old behaviour.
    monkeypatch.setattr(wx, "_weatherapi_key", "")
    _wapi_healthy(**{"/forecast": _429()}).install(monkeypatch)
    assert wx.fetch(CHENNAI).status == "unavailable"


def test_marine_failure_is_still_unavailable_even_with_the_fallback(
    wx: WeatherDataAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    # FR-WX-4 unchanged: wave height has no fallback provider.
    monkeypatch.setattr(wx, "_weatherapi_key", "test-key")
    _wapi_healthy(**{"/forecast": _429(), "/marine": _429()}).install(monkeypatch)
    assert wx.fetch(CHENNAI).status == "unavailable"


def test_fallback_still_reports_alerts_from_the_same_payload(
    wx: WeatherDataAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {
        "current": _WAPI_CURRENT["current"],
        "alerts": {"alert": [{"headline": "Cyclone warning", "severity": "Severe"}]},
    }
    monkeypatch.setattr(wx, "_weatherapi_key", "test-key")
    _healthy(**{"api.weatherapi.com": _json(payload), "/forecast": _429()}).install(monkeypatch)

    res = wx.fetch(CHENNAI)
    assert res.data["forecast_source"] == "weatherapi"
    assert res.data["active_alerts"] == ["Cyclone warning (Severe)"]
    assert res.data["alerts_source_available"] is True


def test_fallback_needs_a_wind_speed_to_be_usable(
    wx: WeatherDataAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wx, "_weatherapi_key", "test-key")
    _healthy(
        **{"api.weatherapi.com": _json({"current": {"vis_km": 10.0}}), "/forecast": _429()}
    ).install(monkeypatch)
    assert wx.fetch(CHENNAI).status == "unavailable"


def test_fallback_maps_the_real_captured_weatherapi_payload() -> None:
    """Against the committed sample capture, not a hand-written stub — so a
    provider field rename is caught here rather than on stage."""
    import json
    from pathlib import Path

    from app.data_access.weather_adapter import _forecast_from_weatherapi

    sample = (
        Path(__file__).resolve().parents[3]
        / "docs" / "samples" / "weather" / "weatherapi_alerts_chennai.json"
    )
    payload = json.loads(sample.read_text(encoding="utf-8"))
    out = _forecast_from_weatherapi(payload)

    assert out is not None
    cur = out["current"]
    assert cur["wind_speed_10m"] == payload["current"]["wind_kph"]
    assert cur["visibility"] == payload["current"]["vis_km"] * 1000.0
    assert cur["time"] == payload["current"]["last_updated_epoch"]


def test_alerts_helper_preserves_none_versus_empty() -> None:
    # NFR-REL-2: None means "couldn't check", [] means "checked, none active".
    from app.data_access.weather_adapter import _wapi_alerts

    assert _wapi_alerts(None) is None
    assert _wapi_alerts({}) == []
    assert _wapi_alerts({"alerts": {"alert": []}}) == []
    assert _wapi_alerts({"alerts": {"alert": [{"headline": "x"}]}}) == [{"headline": "x"}]


# --------------------------------------------------------------------------- #
# 5. Shared-IP escape hatch: the Open-Meteo customer key (#116)
# --------------------------------------------------------------------------- #
# The cache, the cooldown and the WeatherAPI fallback all make the best of a
# quota metered against an IP we do not control. A key is the only thing that
# stops us being metered that way at all.
def test_no_apikey_param_when_no_key_is_configured(
    wx: WeatherDataAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The keyless tier stays the default and byte-identical — an `apikey=` on a
    # free host is at best noise.
    seen: list[dict] = []
    router = _healthy()

    def _spy(url, **kw):
        seen.append(kw.get("params") or {})
        return router(url, **kw)

    monkeypatch.setattr(httpx, "get", _spy)
    wx.fetch(CHENNAI)
    assert seen and all("apikey" not in p for p in seen)


def test_apikey_is_sent_on_forecast_and_marine_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, dict] = {}
    router = _healthy()

    def _spy(url, **kw):
        text = str(url)
        for k in ("/marine", "/forecast"):
            if k in text:
                seen[k] = kw.get("params") or {}
                break
        return router(url, **kw)

    monkeypatch.setattr(httpx, "get", _spy)
    wx = WeatherDataAdapter()
    monkeypatch.setattr(wx, "_open_meteo_key", "sk-test")

    assert wx.fetch(CHENNAI).status == "ok"
    assert seen["/forecast"]["apikey"] == "sk-test"
    assert seen["/marine"]["apikey"] == "sk-test"


def test_key_on_a_free_host_warns_that_it_will_be_ignored(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Open-Meteo honours the key only on `customer-` hosts. A paid key silently
    # doing nothing is an hour of someone's debugging, so say so at startup.
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("OPEN_METEO_API_KEY", "sk-test")
    try:
        with caplog.at_level("WARNING"):
            WeatherDataAdapter()
    finally:
        get_settings.cache_clear()

    assert any("customer-" in r.getMessage() for r in caplog.records)
