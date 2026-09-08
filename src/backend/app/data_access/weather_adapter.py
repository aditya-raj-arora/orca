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
  2. A per-source cooldown after a 429, so an hour/day quota stops us calling
     at all instead of us adding load to an API that is already refusing.
     Shared with GeocodingAdapter (data_access/http_client.py) — it is the
     same provider metered on the same IP, so a 429 there is news here.
  3. A deadline-bounded retry on 5xx / transport errors, which are genuinely
     transient. NOT on 429: #106 retried that too, contradicting its own
     reasoning in (2) — an IP-level minute/hour/day bucket cannot clear inside
     a sub-second backoff, so those attempts only added load to an API that
     had just refused us (#116).
  4. OPEN_METEO_API_KEY, optional and paid: the only thing that stops us being
     metered by IP at all. See core/config.py and docs/DEPLOYMENT.md.

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
from urllib.parse import urlsplit

import httpx

from app.core.config import get_settings
from app.data_access import http_client
from app.data_access.base import AdapterResult, DataSourceAdapter
from app.data_access.http_client import RateLimitedError  # noqa: F401 (re-export)

logger = logging.getLogger(__name__)

_GDACS_TC_RADIUS_KM = 1200.0  # a TC further than this from the query point is
                              # not "active weather" for that location.

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

# GDACS feed body cache (#147). Unlike _RESULT_CACHE this is keyed on nothing:
# the feed is a single global document, identical for every location, so one
# entry serves every query. It is also 1.5 MB, which we were re-downloading per
# query inside a 4s per-attempt budget on a one-core box — the bulk of this
# leg's latency, and the whole of our exposure to a transient unreadable
# response. TTL is short because a cyclone alert going stale is the one thing
# worth spending a fetch on.
_GDACS_FEED_TTL_S = 300.0
_GDACS_FEED_LOCK = threading.Lock()
_GDACS_FEED: tuple[float, str] | None = None
_GDACS_NS = {
    "geo": "http://www.w3.org/2003/01/geo/wgs84_pos#",
    "gdacs": "http://www.gdacs.org",
    "georss": "http://www.georss.org/georss",
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _get(source: str, url: str, **kwargs: Any) -> httpx.Response:
    """Thin alias for the shared policy in data_access/http_client.py, kept so
    the per-source fetchers below read as they always did."""
    return http_client.get(source, url, **kwargs)


def _cooldown_remaining_s(source: str) -> float:
    return http_client.cooldown_remaining_s(source)


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


def _gdacs_feed_get() -> str | None:
    with _GDACS_FEED_LOCK:
        if _GDACS_FEED is None:
            return None
        stored_at, rss_text = _GDACS_FEED
        if time.monotonic() - stored_at > _GDACS_FEED_TTL_S:
            return None
    return rss_text


def _gdacs_feed_put(rss_text: str) -> None:
    global _GDACS_FEED
    with _GDACS_FEED_LOCK:
        _GDACS_FEED = (time.monotonic(), rss_text)


def _reset_rate_limit_state() -> None:
    """Test hook: drop the module-level caches and cooldowns (see
    tests/conftest.py). Not used in production code."""
    global _GDACS_FEED
    with _CACHE_LOCK:
        _RESULT_CACHE.clear()
    with _GDACS_FEED_LOCK:
        _GDACS_FEED = None
    http_client.reset_cooldowns()


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


def _warn_if_not_customer_host(*base_urls: str) -> None:
    """An Open-Meteo key is only honoured on the `customer-` hosts. Pointed at
    the free hosts it is ignored and we keep spending the shared-IP quota — a
    paid key that silently does nothing is exactly the failure someone would
    debug for an hour, so say it once at construction."""
    for base in base_urls:
        host = urlsplit(base).hostname or ""
        if not host.startswith("customer-"):
            logger.warning(
                "WeatherDataAdapter: OPEN_METEO_API_KEY is set but %s is not a "
                "'customer-' host — the key will be ignored and calls will keep "
                "using the shared-IP free quota. See docs/DEPLOYMENT.md.",
                base,
            )


class WeatherDataAdapter(DataSourceAdapter):
    def __init__(self) -> None:
        s = get_settings()
        self._forecast_base = s.weather_forecast_base_url.rstrip("/")
        self._marine_base = s.marine_api_base_url.rstrip("/")
        self._weatherapi_base = s.weatherapi_base_url.rstrip("/")
        self._weatherapi_key = s.weatherapi_key
        self._gdacs_base = s.gdacs_base_url.rstrip("/")
        self._cache_ttl_s = s.weather_cache_ttl_seconds
        # Optional (#116). Set, it moves forecast/marine onto Open-Meteo's
        # customer quota instead of Render's shared egress IP — the only fix
        # that stops us being metered by an IP we do not control. Unset, the
        # keyless endpoints are used exactly as before.
        self._open_meteo_key = (s.open_meteo_api_key or "").strip()
        if self._open_meteo_key:
            _warn_if_not_customer_host(self._forecast_base, self._marine_base)

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
    def _open_meteo_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """Add `apikey` when one is configured. Open-Meteo reads the key from
        this query parameter (not a header), and only on a `customer-` host —
        see _warn_if_not_customer_host."""
        if not self._open_meteo_key:
            return params
        return {**params, "apikey": self._open_meteo_key}

    def _fetch_forecast(self, lat: float, lon: float) -> dict[str, Any]:
        r = _get(
            "open-meteo/forecast",
            f"{self._forecast_base}/forecast",
            params=self._open_meteo_params({
                "latitude": lat,
                "longitude": lon,
                "current": (
                    "wind_speed_10m,wind_gusts_10m,wind_direction_10m,"
                    "precipitation,visibility,weather_code"
                ),
                "timeformat": "unixtime",
                "wind_speed_unit": "kmh",
            }),
        )
        return r.json()

    def _fetch_marine(self, lat: float, lon: float) -> dict[str, Any]:
        r = _get(
            "open-meteo/marine",
            f"{self._marine_base}/marine",
            params=self._open_meteo_params({
                "latitude": lat,
                "longitude": lon,
                "current": "wave_height,wave_direction,wave_period",
                "timeformat": "unixtime",
            }),
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
        _GDACS_TC_RADIUS_KM of (lat, lon). [] is a valid, common result —
        and means "checked, none nearby", never "could not check" (#147).

        The feed body is cached (#147): it is global, identical for every
        location, and 1.5 MB, so fetching it per query was both the bulk of
        this leg's latency and the whole of our exposure to a transient
        unparseable response. Only the fetch is shared; the radius filter still
        runs per query against the caller's coordinates.
        """
        rss_text = _gdacs_feed_get()
        if rss_text is None:
            r = _get(
                "gdacs/rss",
                f"{self._gdacs_base}/rss.xml",
                headers={"User-Agent": "ORCA/prototype (SIH 2026)"},
            )
            rss_text = r.text
            # Cached only after it parses — see _parse_gdacs_tc, which raises
            # on a bad feed rather than pretending it saw no cyclones. Storing
            # first would pin a broken feed for the whole TTL.
            alerts = _parse_gdacs_tc(rss_text, lat, lon)
            _gdacs_feed_put(rss_text)
            return alerts
        return _parse_gdacs_tc(rss_text, lat, lon)

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
    except ET.ParseError as exc:
        # #147: RAISE, do not return []. _normalise() reads None as "source not
        # checked" and [] as "checked, nothing active", and a feed we could not
        # read is emphatically the former. Returning [] here reported an
        # unreadable cyclone feed as "no cyclones nearby" — the exact "empty
        # list read as clear rather than unknown" trap WeatherResult and
        # RiskSafetyAgent both carry docstrings warning about (NFR-REL-2).
        # _safe() turns this into None and the existing unavailable path
        # handles the rest.
        logger.warning("_parse_gdacs_tc: unparseable GDACS RSS feed: %s", exc)
        raise
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
            logger.debug("_parse_gdacs_tc: non-numeric GDACS item coordinates %r/%r", ilat, ilon)
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
        logger.debug("_as_float: non-numeric value %r", v)
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
        logger.debug("_as_epoch: unparseable timestamp value %r", v)
        return None
