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
"""
from __future__ import annotations

import logging
import math
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.config import get_settings
from app.data_access.base import AdapterResult, DataSourceAdapter

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT_S = 4.0          # per call; 4 calls run in parallel, so well
                              # inside the graph's 6s AGENT_TIMEOUT_SECONDS.
_GDACS_TC_RADIUS_KM = 1200.0  # a TC further than this from the query point is
                              # not "active weather" for that location.
_GDACS_NS = {
    "geo": "http://www.w3.org/2003/01/geo/wgs84_pos#",
    "gdacs": "http://www.gdacs.org",
    "georss": "http://www.georss.org/georss",
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


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

        with ThreadPoolExecutor(max_workers=4) as pool:
            f_forecast = pool.submit(self._safe, self._fetch_forecast, lat, lon)
            f_marine = pool.submit(self._safe, self._fetch_marine, lat, lon)
            f_wapi = pool.submit(self._safe, self._fetch_weatherapi_alerts, lat, lon)
            f_gdacs = pool.submit(self._safe, self._fetch_gdacs_tc, lat, lon)
            forecast = f_forecast.result()
            marine = f_marine.result()
            wapi_alerts = f_wapi.result()
            gdacs_alerts = f_gdacs.result()

        # FR-WX-4: forecast and marine are both required for a WeatherResult
        # (wind AND wave). Missing either -> unavailable, never fabricated.
        if forecast is None or marine is None:
            return AdapterResult(data=None, fetched_at=now, status="unavailable")

        data = _normalise(forecast, marine, wapi_alerts, gdacs_alerts)
        if data is None:
            return AdapterResult(data=None, fetched_at=now, status="unavailable")
        return AdapterResult(data=data, fetched_at=now, status="ok")

    # ------------------------------------------------------------------ #
    # Per-source fetchers — each returns a plain dict / list, or raises
    # (the raise is caught by _safe and turned into None).
    # ------------------------------------------------------------------ #
    def _fetch_forecast(self, lat: float, lon: float) -> dict[str, Any]:
        r = httpx.get(
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
            timeout=_HTTP_TIMEOUT_S,
        )
        r.raise_for_status()
        return r.json()

    def _fetch_marine(self, lat: float, lon: float) -> dict[str, Any]:
        r = httpx.get(
            f"{self._marine_base}/marine",
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "wave_height,wave_direction,wave_period",
                "timeformat": "unixtime",
            },
            timeout=_HTTP_TIMEOUT_S,
        )
        r.raise_for_status()
        return r.json()

    def _fetch_weatherapi_alerts(self, lat: float, lon: float) -> list[dict[str, Any]] | None:
        """Returns the raw alert objects, [] if none active, or None if we
        could not check (no key configured / request failed)."""
        if not self._weatherapi_key:
            return None
        r = httpx.get(
            f"{self._weatherapi_base}/forecast.json",
            params={
                "key": self._weatherapi_key,
                "q": f"{lat},{lon}",
                "days": 3,
                "alerts": "yes",
                "aqi": "no",
            },
            timeout=_HTTP_TIMEOUT_S,
        )
        r.raise_for_status()
        return r.json().get("alerts", {}).get("alert", []) or []

    def _fetch_gdacs_tc(self, lat: float, lon: float) -> list[dict[str, Any]]:
        """GDACS GeoRSS -> the active tropical cyclones within
        _GDACS_TC_RADIUS_KM of (lat, lon). [] is a valid, common result."""
        r = httpx.get(
            f"{self._gdacs_base}/rss.xml",
            timeout=_HTTP_TIMEOUT_S,
            headers={"User-Agent": "ORCA/prototype (SIH 2026)"},
        )
        r.raise_for_status()
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
) -> dict[str, Any] | None:
    """Merge the raw provider payloads into one flat dict for WeatherAgent.
    Returns None if the two mandatory fields (wind speed, wave height) are
    absent — the agent maps that to status='unavailable'."""
    fcur = forecast.get("current") or {}
    mcur = marine.get("current") or {}

    wind = fcur.get("wind_speed_10m")
    wave = mcur.get("wave_height")
    if wind is None or wave is None:
        return None

    epoch = _as_epoch(fcur.get("time") if fcur.get("time") is not None else mcur.get("time"))
    alerts_checked = (wapi_alerts is not None) or (gdacs_alerts is not None)

    return {
        # FR-WX-1
        "wind_speed_kmh": float(wind),
        "wind_gust_kmh": _as_float(fcur.get("wind_gusts_10m")),
        "wind_direction_deg": _as_float(fcur.get("wind_direction_10m")),
        "precipitation_mm": _as_float(fcur.get("precipitation")),
        "visibility_m": _as_float(fcur.get("visibility")),
        "weather_code": fcur.get("weather_code"),
        "wave_height_m": float(wave),
        "wave_period_s": _as_float(mcur.get("wave_period")),
        "wave_direction_deg": _as_float(mcur.get("wave_direction")),
        # FR-WX-3
        "data_time_epoch": epoch,
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
