"""
WeatherDataAdapter — the ONLY module permitted to know the weather providers'
specific API shapes. Confirmed sources (docs/p3-data-source-spike.md §2):

  * Open-Meteo Forecast API  — wind / precipitation / visibility (FR-WX-1). No key.
  * Open-Meteo Marine API    — wave height / period / direction (FR-WX-1). No key.
  * WeatherAPI.com           — government severe-weather / cyclone alerts
                               (FR-WX-2), lat/lon native. Free key, no card.
  * GDACS GeoRSS             — tropical-cyclone events, North Indian Ocean
                               (FR-WX-2). No key.

Owner: P3 (Weather & Ocean Data Engineer).
Reference: LLD v1.0 §2.9. Implements the fetch() half of FR-WX-1..4.

Contract (LLD §2.9): fetch() NEVER raises. On any failure of a *required*
source (forecast or marine) it returns AdapterResult(status='unavailable') —
no partial / fabricated data (FR-WX-4). The two alert sources are best-effort:
if both fail, the result is still 'ok' but data['alerts_source_available'] is
False so the agent / Synthesis can say "alert data unavailable" rather than
imply "no alerts" (NFR-REL-1).

Rate limiting (#106): Open-Meteo's keyless tier is quota'd per minute / hour /
day and answers a burst with 429. Three things keep us inside it, in order of
how much they actually buy:

  1. A short-TTL result cache keyed on a coarse lat/lon grid, so N queries
     about the same harbour inside the TTL cost one upstream call.
  2. A deadline-bounded retry on 429/5xx that honours Retry-After — rides out
     a per-minute bucket without ever exceeding AGENT_TIMEOUT_SECONDS.
  3. A per-source cooldown after a 429, so an hour/day quota stops us calling
     at all instead of us adding load to an API that is already refusing.

None of these ever soften the FR-WX-4 contract: a cache hit is real data with
its real observation timestamp, and everything else still degrades to
'unavailable' rather than to a fabricated number.

Provider fallback (#116): on Render the forecast leg 429s *persistently*, not
in bursts — Open-Meteo rate-limits per IP and Render's free plan shares its
egress IP with other tenants, so retry and cache cannot help. WeatherAPI's
forecast.json response, which we already fetch for alerts, carries a `current`
block with the same wind/precipitation/visibility fields, so when Open-Meteo's
forecast fails we derive the forecast leg from that payload instead: a second
use of a response already in hand, on a different host with a per-key quota.
Wave height has no such fallback (WeatherAPI marine is a separate endpoint), so
a marine failure still means 'unavailable'.
"""
from __future__ import annotations

import logging
import math
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.config import get_settings
from app.data_access.base import AdapterResult, DataSourceAdapter

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT_S = 4.0          # per attempt; 4 sources run in parallel, so well
                              # inside the graph's 6s AGENT_TIMEOUT_SECONDS.
_SOURCE_BUDGET_S = 5.0        # total wall clock for one source INCLUDING its
                              # retries. Every retry is bounded by this
                              # deadline, so hardening the adapter against 429
                              # can never push a node past AGENT_TIMEOUT_SECONDS
                              # (orchestration/graph.py) — a source that runs
                              # out of budget simply reports failure early.
_MAX_ATTEMPTS = 3
_BACKOFF_BASE_S = 0.25        # 0.25s, 0.5s — deliberately short: the budget
_MAX_BACKOFF_S = 1.0          # above, not the backoff curve, is the real bound.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_GDACS_TC_RADIUS_KM = 1200.0  # a TC further than this from the query point is
                              # not "active weather" for that location.

# --- 429 cooldown ------------------------------------------------------- #
# A 429 from an hourly/daily quota does not clear in a backoff window, and
# retrying into it adds load to an API that is already refusing us. After a 429
# we stop calling that source until Retry-After (or _DEFAULT_COOLDOWN_S when
# the header is absent) has passed. Capped so a hostile/garbled header cannot
# park a source for the rest of the demo.
_DEFAULT_COOLDOWN_S = 30.0
_MAX_COOLDOWN_S = 300.0
_COOLDOWN_LOCK = threading.Lock()
_COOLDOWN: dict[str, float] = {}  # source label -> time.monotonic() deadline

# --- result cache ------------------------------------------------------- #
# Open-Meteo runs an ~11 km model grid refreshed roughly every 15 min, so two
# queries about the same harbour inside the TTL would be answered from the same
# grid cell with the same numbers — the second upstream call buys nothing and
# spends quota. 0.05 deg (~5.5 km) is finer than the provider's own grid, so
# collapsing to it cannot merge conditions the provider would have reported
# differently.
#
# Module-level, not instance-level, for the same reason as INCOISAdapter's PFZ
# cache: build_orchestration_graph() constructs a fresh WeatherDataAdapter per
# request (graph.py run_query's TODO(P1)), so an instance attribute would never
# hit. ONLY status='ok' results are cached — a transient failure must not get
# stuck for the whole TTL (NFR-REL-1). Cached entries keep their original
# fetched_at and data_time_epoch, so FR-WX-3 still surfaces the true
# observation time rather than the time of the cache hit.
_CACHE_GRID_DEG = 0.05
_CACHE_MAX_ENTRIES = 256
_CACHE_LOCK = threading.Lock()
_RESULT_CACHE: dict[tuple[float, float], tuple[float, AdapterResult]] = {}
_GDACS_NS = {
    "geo": "http://www.w3.org/2003/01/geo/wgs84_pos#",
    "gdacs": "http://www.gdacs.org",
    "georss": "http://www.georss.org/georss",
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


class RateLimitedError(Exception):
    """Raised instead of issuing a request to a source that is inside its 429
    cooldown. Caught by _safe like any other source failure — the point is to
    fail without adding load, not to fail differently."""


# ---------------------------------------------------------------------- #
# Rate-limit handling (#106)
# ---------------------------------------------------------------------- #
def _cooldown_remaining_s(source: str) -> float:
    with _COOLDOWN_LOCK:
        until = _COOLDOWN.get(source)
    return 0.0 if until is None else max(0.0, until - time.monotonic())


def _start_cooldown(source: str, retry_after_s: float | None) -> None:
    delay = _DEFAULT_COOLDOWN_S if retry_after_s is None else retry_after_s
    delay = min(max(delay, 0.0), _MAX_COOLDOWN_S)
    with _COOLDOWN_LOCK:
        _COOLDOWN[source] = time.monotonic() + delay
    logger.warning(
        "WeatherDataAdapter: %s rate-limited (429) — pausing calls to it for %.0fs",
        source,
        delay,
    )


def _retry_after_s(response: httpx.Response) -> float | None:
    """RFC 9110 Retry-After, delta-seconds form only. The HTTP-date form is
    rare on rate limiters and is not worth a clock-skew bug here — treating it
    as absent just falls back to _DEFAULT_COOLDOWN_S."""
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw.strip()))
    except ValueError:
        return None


def _get(source: str, url: str, **kwargs: Any) -> httpx.Response:
    """httpx.get hardened against transient upstream refusal.

    Retries 429 / 5xx / transport errors up to _MAX_ATTEMPTS, honouring
    Retry-After, with every attempt AND every sleep bounded by a
    _SOURCE_BUDGET_S deadline. Raises on final failure (the caller's _safe
    turns that into None, i.e. the existing degrade path) — a non-retryable
    4xx still raises on the first attempt exactly as raise_for_status() did.
    """
    cooling = _cooldown_remaining_s(source)
    if cooling > 0:
        raise RateLimitedError(f"{source}: in 429 cooldown for another {cooling:.0f}s")

    deadline = time.monotonic() + _SOURCE_BUDGET_S
    last_error: Exception | None = None

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        retry_after: float | None = None
        try:
            response = httpx.get(url, timeout=min(_HTTP_TIMEOUT_S, remaining), **kwargs)
        except httpx.TransportError as exc:  # connect/read/write/pool errors
            last_error = exc
        else:
            if response.status_code not in _RETRYABLE_STATUS:
                response.raise_for_status()  # non-retryable 4xx -> raise as before
                return response
            retry_after = _retry_after_s(response)
            if response.status_code == 429:
                _start_cooldown(source, retry_after)
            last_error = httpx.HTTPStatusError(
                f"{source}: retryable {response.status_code} from {url}",
                request=response.request,
                response=response,
            )

        if attempt == _MAX_ATTEMPTS:
            break
        delay = min(_MAX_BACKOFF_S, _BACKOFF_BASE_S * (2 ** (attempt - 1)))
        if retry_after is not None:
            delay = max(delay, retry_after)  # the server's number wins if larger
        if delay >= deadline - time.monotonic():
            break  # no budget left for another attempt; give up now, don't oversleep
        time.sleep(delay)

    assert last_error is not None  # loop only exits early after setting it
    raise last_error


# ---------------------------------------------------------------------- #
# Result cache (#106)
# ---------------------------------------------------------------------- #
def _cache_key(lat: float, lon: float) -> tuple[float, float]:
    g = _CACHE_GRID_DEG
    return (round(lat / g) * g, round(lon / g) * g)


def _cache_get(key: tuple[float, float], ttl_s: float) -> AdapterResult | None:
    if ttl_s <= 0:
        return None
    now = time.monotonic()
    with _CACHE_LOCK:
        entry = _RESULT_CACHE.get(key)
        if entry is None:
            return None
        stored_at, result = entry
        if now - stored_at > ttl_s:
            del _RESULT_CACHE[key]
            return None
    return result


def _cache_put(key: tuple[float, float], result: AdapterResult, ttl_s: float) -> None:
    if ttl_s <= 0:
        return
    now = time.monotonic()
    with _CACHE_LOCK:
        _RESULT_CACHE[key] = (now, result)
        if len(_RESULT_CACHE) > _CACHE_MAX_ENTRIES:
            for k in [k for k, (t, _) in _RESULT_CACHE.items() if now - t > ttl_s]:
                del _RESULT_CACHE[k]
        if len(_RESULT_CACHE) > _CACHE_MAX_ENTRIES:  # still full: drop the oldest
            oldest = min(_RESULT_CACHE, key=lambda k: _RESULT_CACHE[k][0])
            del _RESULT_CACHE[oldest]


def _reset_rate_limit_state() -> None:
    """Test hook: drop the module-level cache and cooldowns (see
    tests/conftest.py). Not used in production code."""
    with _CACHE_LOCK:
        _RESULT_CACHE.clear()
    with _COOLDOWN_LOCK:
        _COOLDOWN.clear()


def _rough_haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance, km. Local copy for the GDACS proximity gate only.
    The canonical implementation is app.agents.ocean_agent.haversine_km (LLD
    §4.3, reused by Geofencing); this coarse "is a cyclone near me" check does
    not warrant a data_access -> agents import."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class WeatherDataAdapter(DataSourceAdapter):
    def __init__(self) -> None:
        s = get_settings()
        self._forecast_base = s.weather_forecast_base_url.rstrip("/")
        self._marine_base = s.marine_api_base_url.rstrip("/")
        self._weatherapi_base = s.weatherapi_base_url.rstrip("/")
        self._weatherapi_key = s.weatherapi_key
        self._gdacs_base = s.gdacs_base_url.rstrip("/")
        self._cache_ttl_s = s.weather_cache_ttl_seconds

    # ------------------------------------------------------------------ #
    # Public contract
    # ------------------------------------------------------------------ #
    def fetch(self, params: dict[str, Any]) -> AdapterResult:
        """params: {"lat": float, "lon": float, "window": TimeWindow | None}.
        window is currently unused (we report current conditions + active
        alerts); it is accepted so the signature is stable for the forecast
        range work in a later sprint."""
        now = _utcnow()
        try:
            lat = float(params["lat"])
            lon = float(params["lon"])
        except (KeyError, TypeError, ValueError):
            logger.warning("WeatherDataAdapter.fetch: bad params %r", params)
            return AdapterResult(data=None, fetched_at=now, status="unavailable")

        key = _cache_key(lat, lon)
        cached = _cache_get(key, self._cache_ttl_s)
        if cached is not None:
            # Real data with its real timestamps (FR-WX-3) — see the cache note
            # at the top of this module. Only 'ok' results are ever stored.
            logger.debug("WeatherDataAdapter: cache hit for %s", key)
            return cached

        with ThreadPoolExecutor(max_workers=4) as pool:
            f_forecast = pool.submit(self._safe, self._fetch_forecast, lat, lon)
            f_marine = pool.submit(self._safe, self._fetch_marine, lat, lon)
            f_wapi = pool.submit(self._safe, self._fetch_weatherapi, lat, lon)
            f_gdacs = pool.submit(self._safe, self._fetch_gdacs_tc, lat, lon)
            forecast = f_forecast.result()
            marine = f_marine.result()
            wapi_payload = f_wapi.result()
            gdacs_alerts = f_gdacs.result()

        wapi_alerts = _wapi_alerts(wapi_payload)
        forecast_source = "open-meteo"

        if forecast is None:
            # #116: Open-Meteo's forecast leg 429s persistently from Render's
            # shared egress IP. The WeatherAPI payload already in hand carries
            # the same current-conditions fields, so use it rather than losing
            # the whole result (and with it the safety verdict).
            fallback = _forecast_from_weatherapi(wapi_payload)
            if fallback is not None:
                logger.warning(
                    "WeatherDataAdapter: Open-Meteo forecast unavailable — serving "
                    "WeatherAPI current conditions for the forecast leg (#116)"
                )
                forecast, forecast_source = fallback, "weatherapi"

        # FR-WX-4: forecast and marine are both required for a WeatherResult
        # (wind AND wave). Missing either -> unavailable, never fabricated.
        # Wave height has no fallback provider, so marine is still absolute.
        if forecast is None or marine is None:
            return AdapterResult(data=None, fetched_at=now, status="unavailable")

        try:
            data = _normalise(forecast, marine, wapi_alerts, gdacs_alerts, forecast_source)
        except Exception as exc:  # noqa: BLE001 - LLD §2.9: never raise on a bad payload shape
            logger.warning("WeatherDataAdapter: normalise failed on %s", exc)
            data = None
        if data is None:
            return AdapterResult(data=None, fetched_at=now, status="unavailable")

        result = AdapterResult(data=data, fetched_at=now, status="ok")
        _cache_put(key, result, self._cache_ttl_s)
        return result

    # ------------------------------------------------------------------ #
    # Per-source fetchers — each returns a plain dict / list, or raises
    # (the raise is caught by _safe and turned into None).
    # ------------------------------------------------------------------ #
    def _fetch_forecast(self, lat: float, lon: float) -> dict[str, Any]:
        r = _get(
            "open-meteo/forecast",
            f"{self._forecast_base}/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "current": (
                    "wind_speed_10m,wind_gusts_10m,wind_direction_10m,"
                    "precipitation,visibility,weather_code"
                ),
                "timeformat": "unixtime",
                "wind_speed_unit": "kmh",
            },
        )
        return r.json()

    def _fetch_marine(self, lat: float, lon: float) -> dict[str, Any]:
        r = _get(
            "open-meteo/marine",
            f"{self._marine_base}/marine",
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "wave_height,wave_direction,wave_period",
                "timeformat": "unixtime",
            },
        )
        return r.json()

    def _fetch_weatherapi(self, lat: float, lon: float) -> dict[str, Any] | None:
        """The whole forecast.json payload, or None if we could not fetch it
        (no key configured / request failed).

        Deliberately not narrowed to alerts: the same response also carries the
        `current` block used as the forecast fallback (#116), and fetching it
        twice would spend two calls for one payload."""
        if not self._weatherapi_key:
            return None
        r = _get(
            "weatherapi/alerts",
            f"{self._weatherapi_base}/forecast.json",
            params={
                "key": self._weatherapi_key,
                "q": f"{lat},{lon}",
                "days": 3,
                "alerts": "yes",
                "aqi": "no",
            },
        )
        return r.json()

    def _fetch_gdacs_tc(self, lat: float, lon: float) -> list[dict[str, Any]]:
        """GDACS GeoRSS -> the active tropical cyclones within
        _GDACS_TC_RADIUS_KM of (lat, lon). [] is a valid, common result."""
        r = _get(
            "gdacs/rss",
            f"{self._gdacs_base}/rss.xml",
            headers={"User-Agent": "ORCA/prototype (SIH 2026)"},
        )
        return _parse_gdacs_tc(r.text, lat, lon)

    # ------------------------------------------------------------------ #
    @staticmethod
    def _safe(fn: Any, *args: Any) -> Any:
        try:
            return fn(*args)
        except Exception as exc:  # noqa: BLE001 - LLD §2.9: degrade, never raise
            logger.warning("WeatherDataAdapter: %s failed: %s", getattr(fn, "__name__", fn), exc)
            return None


# ---------------------------------------------------------------------- #
# Pure helpers (no I/O) — unit-tested directly against captured samples.
# ---------------------------------------------------------------------- #
def _gdacs_item_latlon(item: ET.Element) -> tuple[str | None, str | None]:
    """GDACS puts the point in <geo:Point><geo:lat/><geo:long/></geo:Point>,
    with <georss:point>"lat lon"</georss:point> as a fallback."""
    pt = item.find("geo:Point", _GDACS_NS)
    if pt is not None:
        ilat = pt.findtext("geo:lat", namespaces=_GDACS_NS)
        ilon = pt.findtext("geo:long", namespaces=_GDACS_NS)
        if ilat and ilon:
            return ilat, ilon
    grp = (item.findtext("georss:point", namespaces=_GDACS_NS) or "").split()
    if len(grp) == 2:
        return grp[0], grp[1]
    return None, None


def _parse_gdacs_tc(rss_text: str, lat: float, lon: float) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        root = ET.fromstring(rss_text)
    except ET.ParseError:
        return out
    for item in root.iterfind(".//item"):
        etype = item.findtext("gdacs:eventtype", default="", namespaces=_GDACS_NS)
        if etype != "TC":
            continue
        ilat, ilon = _gdacs_item_latlon(item)
        if ilat is None or ilon is None:
            continue
        try:
            dist = _rough_haversine_km(lat, lon, float(ilat), float(ilon))
        except ValueError:
            continue
        if dist > _GDACS_TC_RADIUS_KM:
            continue
        name = item.findtext("gdacs:eventname", default="", namespaces=_GDACS_NS).strip()
        level = item.findtext("gdacs:alertlevel", default="", namespaces=_GDACS_NS).strip()
        title = (item.findtext("title") or "").strip()
        out.append(
            {
                "source": "GDACS",
                "event": "Tropical Cyclone",
                "name": name or None,
                "severity": level or None,
                "distance_km": round(dist, 1),
                "headline": title or f"Tropical cyclone {name} ({level})",
            }
        )
    return out


def _wapi_alerts(payload: dict[str, Any] | None) -> list[dict[str, Any]] | None:
    """Alert objects out of a forecast.json payload: [] when none are active,
    None when we could not check at all (no key / request failed). The None vs
    [] distinction drives alerts_source_available (NFR-REL-2), so it must
    survive the widening of the fetcher to the whole payload."""
    if payload is None:
        return None
    return (payload.get("alerts") or {}).get("alert") or []


def _forecast_from_weatherapi(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """WeatherAPI `current` -> the same shape Open-Meteo's forecast endpoint
    returns, so _normalise() consumes it unchanged (#116).

    None if the payload carries no usable wind speed — the fallback must be
    able to fail, and a fallback that cannot supply the core field is not one.
    """
    if payload is None:
        return None
    cur = payload.get("current") or {}
    wind = _as_float(cur.get("wind_kph"))
    if wind is None:
        return None
    vis_km = _as_float(cur.get("vis_km"))
    return {
        "current": {
            "wind_speed_10m": wind,
            "wind_gusts_10m": _as_float(cur.get("gust_kph")),
            "wind_direction_10m": _as_float(cur.get("wind_degree")),
            "precipitation": _as_float(cur.get("precip_mm")),
            "visibility": vis_km * 1000.0 if vis_km is not None else None,
            # NOT mapped from condition.code: WeatherAPI uses its own condition
            # scheme, not the WMO codes Open-Meteo returns, so passing it
            # through would be a number that silently means something else.
            # Nothing consumes weather_code today.
            "weather_code": None,
            "time": cur.get("last_updated_epoch"),
        }
    }


def _alert_strings(
    wapi_alerts: list[dict[str, Any]] | None,
    gdacs_alerts: list[dict[str, Any]] | None,
) -> list[str]:
    """Flatten both alert sources to the list[str] WeatherResult.active_alerts
    expects. Order: WeatherAPI first, then GDACS."""
    strings: list[str] = []
    for a in wapi_alerts or []:
        head = (a.get("headline") or a.get("event") or "").strip()
        sev = (a.get("severity") or "").strip()
        if head:
            strings.append(f"{head} ({sev})" if sev else head)
    for a in gdacs_alerts or []:
        head = (a.get("headline") or "").strip()
        dist = a.get("distance_km")
        if head:
            strings.append(f"{head} — ~{dist} km away" if dist is not None else head)
    return strings


def _normalise(
    forecast: dict[str, Any],
    marine: dict[str, Any],
    wapi_alerts: list[dict[str, Any]] | None,
    gdacs_alerts: list[dict[str, Any]] | None,
    forecast_source: str = "open-meteo",
) -> dict[str, Any] | None:
    """Merge the raw provider payloads into one flat dict for WeatherAgent.
    Returns None if the two mandatory fields (wind speed, wave height) are
    absent — the agent maps that to status='unavailable'."""
    fcur = forecast.get("current") or {}
    mcur = marine.get("current") or {}

    wind = _as_float(fcur.get("wind_speed_10m"))
    wave = _as_float(mcur.get("wave_height"))
    if wind is None or wave is None:
        # core fields absent or non-numeric -> not a usable WeatherResult
        return None

    epoch = _as_epoch(fcur.get("time") if fcur.get("time") is not None else mcur.get("time"))
    alerts_checked = (wapi_alerts is not None) or (gdacs_alerts is not None)

    return {
        # FR-WX-1
        "wind_speed_kmh": wind,
        "wind_gust_kmh": _as_float(fcur.get("wind_gusts_10m")),
        "wind_direction_deg": _as_float(fcur.get("wind_direction_10m")),
        "precipitation_mm": _as_float(fcur.get("precipitation")),
        "visibility_m": _as_float(fcur.get("visibility")),
        "weather_code": fcur.get("weather_code"),
        "wave_height_m": wave,
        "wave_period_s": _as_float(mcur.get("wave_period")),
        "wave_direction_deg": _as_float(mcur.get("wave_direction")),
        # FR-WX-3
        "data_time_epoch": epoch,
        # Which provider supplied the wind/precipitation/visibility leg (#116).
        # Observability only — no agent branches on it — but a run served by the
        # fallback must not be indistinguishable from a normal one.
        "forecast_source": forecast_source,
        # FR-WX-2
        "active_alerts": _alert_strings(wapi_alerts, gdacs_alerts),
        "alerts_source_available": alerts_checked,
        "alerts_raw": {"weatherapi": wapi_alerts or [], "gdacs": gdacs_alerts or []},
    }


def _as_float(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _as_epoch(v: Any) -> int | None:
    """Accept a unix epoch (adapter asks for timeformat=unixtime) or, for
    tolerance, an ISO-8601 string. Naive ISO is assumed UTC."""
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        pass
    try:
        s = str(v)
        if not (s.endswith("Z") or "+" in s[10:] or s[10:].startswith("-")):
            s += "+00:00"
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None
