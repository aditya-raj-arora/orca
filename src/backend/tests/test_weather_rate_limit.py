"""
Rate-limit hardening for WeatherDataAdapter (issue #106, FR-WX-1/3/4,
NFR-REL-1, NFR-PERF-2).

Production symptom: Open-Meteo's keyless tier answered the forecast call with
`429 Too Many Requests`, which the FR-WX-4 contract correctly turned into
status='unavailable' — i.e. no weather at all for that query. These tests pin
the three mitigations (result cache, bounded retry, 429 cooldown) AND pin the
guarantees they must not break: never a fabricated value, never longer than
the graph's AGENT_TIMEOUT_SECONDS budget.

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
    # #116, the correction to #106. Open-Meteo's 429 is an IP-level minute /
    # hour / day bucket; it cannot clear inside our sub-second backoff, so the
    # extra attempts could only add load to an API already refusing us — and on
    # Render's shared egress IP that load is precisely what keeps the bucket
    # empty. Exactly one call, then out.
    router = _healthy(**{"/forecast": _429()}).install(monkeypatch)

    res = wx.fetch(CHENNAI)

    assert res.status == "unavailable"
    assert router.calls["/forecast"] == 1


def test_5xx_is_still_retried_and_recovers(
    wx: WeatherDataAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A 502 is congestion, not a quota — it is genuinely worth another attempt,
    # and dropping the 429 retry must not have dropped this one too.
    router = _healthy(
        **{"/forecast": _then(_502, _json(_GOOD_FORECAST))}
    ).install(monkeypatch)

    res = wx.fetch(CHENNAI)
    assert res.status == "ok"
    assert res.data["wind_speed_kmh"] == 12.0
    assert router.calls["/forecast"] == 2


def test_persistent_429_is_unavailable_never_fabricated(
    wx: WeatherDataAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    # FR-WX-4 unchanged: refusing to retry still degrades honestly rather than
    # inventing a wind speed.
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
    # loop must stay inside it no matter how many attempts the upstream buys.
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
# 4. Shared-IP escape hatch: the Open-Meteo customer key (#116)
# --------------------------------------------------------------------------- #
def test_no_apikey_param_when_no_key_is_configured(
    wx: WeatherDataAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The keyless tier is the default and must stay byte-identical — an
    # `apikey=` on a free host is at best noise.
    seen: list[dict] = []
    router = _healthy().install(monkeypatch)
    original = router.__call__

    def _spy(url, **kw):
        seen.append(kw.get("params") or {})
        return original(url, **kw)

    monkeypatch.setattr(httpx, "get", _spy)
    wx.fetch(CHENNAI)
    assert all("apikey" not in p for p in seen)


def test_apikey_is_sent_on_forecast_and_marine_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The only mitigation that actually escapes a shared egress IP: the quota
    # follows the key instead of the IP.
    seen: dict[str, dict] = {}
    router = _healthy()

    def _spy(url, **kw):
        s = str(url)
        for k in ("/marine", "/forecast"):
            if k in s:
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
    # A paid key silently doing nothing is an hour of someone's debugging.
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("OPEN_METEO_API_KEY", "sk-test")
    try:
        with caplog.at_level("WARNING"):
            WeatherDataAdapter()
    finally:
        get_settings.cache_clear()

    assert any("customer-" in r.getMessage() for r in caplog.records)


# --------------------------------------------------------------------------- #
# 5. WeatherAPI fallback for the rate-limited forecast leg (#116)
# --------------------------------------------------------------------------- #
# The production failure this whole issue is about: api.open-meteo.com 429s
# from Render's shared egress IP while marine-api.open-meteo.com (a different
# host, its own budget) is fine. Forecast is required by FR-WX-4, so losing it
# alone took the entire WeatherResult down and forced Risk to
# INSUFFICIENT_DATA. WeatherAPI's forecast.json — already fetched for alerts,
# on a per-key quota our IP cannot exhaust — carries a `current` block that
# covers the same fields.
_WAPI_WITH_CURRENT = {
    "current": {
        "wind_kph": 18.4,
        "gust_kph": 27.0,
        "wind_degree": 245,
        "precip_mm": 0.3,
        "vis_km": 9.0,
        "condition": {"code": 1003},  # WeatherAPI's own scheme — must NOT leak
        "last_updated_epoch": 1788264000,
    },
    "alerts": {"alert": []},
}


@pytest.fixture()
def wx_keyed(monkeypatch: pytest.MonkeyPatch) -> WeatherDataAdapter:
    adapter = WeatherDataAdapter()
    monkeypatch.setattr(adapter, "_weatherapi_key", "test-key")
    return adapter


def test_forecast_429_falls_back_to_weatherapi_instead_of_going_unavailable(
    wx_keyed: WeatherDataAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    router = _healthy(
        **{
            "/forecast": _429(),
            "api.weatherapi.com": _json(_WAPI_WITH_CURRENT),
        }
    ).install(monkeypatch)

    res = wx_keyed.fetch(CHENNAI)

    assert res.status == "ok"  # the whole point of #116
    assert res.data["wind_speed_kmh"] == 18.4
    assert res.data["wind_gust_kmh"] == 27.0
    assert res.data["wind_direction_deg"] == 245
    assert res.data["precipitation_mm"] == 0.3
    assert res.data["visibility_m"] == 9000.0  # vis_km -> m
    assert res.data["data_time_epoch"] == 1788264000
    assert res.data["wave_height_m"] == 1.1  # still Open-Meteo marine
    assert res.data["forecast_source"] == "weatherapi"
    # Zero extra HTTP requests: the fallback re-reads the alerts response.
    assert router.calls["api.weatherapi.com"] == 1


def test_fallback_does_not_leak_weatherapi_condition_codes_as_wmo(
    wx_keyed: WeatherDataAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1003 is "Partly cloudy" to WeatherAPI and "Mainly clear" to WMO. Passing
    # it through under `weather_code` would be a number that silently means
    # something else — fabricated data arriving via the units (NFR-REL-1).
    _healthy(
        **{"/forecast": _429(), "api.weatherapi.com": _json(_WAPI_WITH_CURRENT)}
    ).install(monkeypatch)

    res = wx_keyed.fetch(CHENNAI)
    assert res.data["weather_code"] is None


def test_open_meteo_forecast_still_wins_when_it_is_healthy(
    wx_keyed: WeatherDataAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The fallback is a standby, not a replacement: WMO codes and the native
    # grid are better data when we can get them.
    _healthy(**{"api.weatherapi.com": _json(_WAPI_WITH_CURRENT)}).install(monkeypatch)

    res = wx_keyed.fetch(CHENNAI)
    assert res.data["wind_speed_kmh"] == 12.0  # _GOOD_FORECAST, not 18.4
    assert res.data["forecast_source"] == "open-meteo"


def test_no_fallback_available_still_degrades_honestly(
    wx_keyed: WeatherDataAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    # FR-WX-4 is untouched. WeatherAPI reachable but with no `current` block
    # (or no key at all) means there is nothing to stand in with — and a made-up
    # wind speed is still worse than "unavailable".
    _healthy(
        **{"/forecast": _429(), "api.weatherapi.com": _json({"alerts": {"alert": []}})}
    ).install(monkeypatch)

    res = wx_keyed.fetch(CHENNAI)
    assert res.status == "unavailable"
    assert res.data is None


def test_marine_429_is_unavailable_even_with_the_fallback(
    wx_keyed: WeatherDataAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    # WeatherAPI's marine data is a separate endpoint we do not call, so there
    # is no standby for wave height — FR-WX-4 still means no WeatherResult.
    _healthy(
        **{"/marine": _429(), "api.weatherapi.com": _json(_WAPI_WITH_CURRENT)}
    ).install(monkeypatch)

    res = wx_keyed.fetch(CHENNAI)
    assert res.status == "unavailable"
