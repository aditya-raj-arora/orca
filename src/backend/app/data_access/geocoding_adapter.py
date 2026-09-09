"""
GeocodingAdapter — place name -> (lat, lon), the step Figure 1's "Location
resolvable?" diamond was always missing (#110).

Source: Open-Meteo Geocoding API (https://geocoding-api.open-meteo.com/v1/search).
Keyless, and the same provider already used for forecast/marine, so this adds
no new credential (NFR-SEC-2) and no new vendor dependency.

Owner: P1 (added for the Planner), sitting in P3/P4's data_access/ area and
following its contract — including, since #116, its shared outbound-HTTP policy
(data_access/http_client.py): bounded retry, Retry-After, and a 429 cooldown.
Open-Meteo meters per client IP, and on Render that IP is shared, so every
Open-Meteo caller in the codebase has to back off — not just the weather one.

Contract (LLD §2.9): fetch() NEVER raises. A name we cannot resolve returns
AdapterResult(status='unavailable') with data=None — the Planner then asks
Figure 1's clarifying question rather than guessing a location.

COUNTRY BIAS — do not remove without reading this. Open-Meteo's global ranking
answers "Kochi" with Kochi, JAPAN (33.55, 133.53); Kochi, India (9.94, 76.26)
is only the second result. For a marine-safety system advising Indian
fishermen, silently returning conditions for the wrong country is far worse
than returning "I don't know" — it is the fabricated-data failure mode
NFR-REL-1 exists to prevent, just arriving via the location instead of the
value. So queries are constrained to `countryCode` (settings.geocoding_country_code,
default IN). Set it to "" to search globally, and understand what you are
turning off.
"""
from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from typing import Any

from app.core.config import get_settings
from app.data_access import http_client
from app.data_access.base import AdapterResult, DataSourceAdapter

logger = logging.getLogger(__name__)

_RESULT_COUNT = 5  # a few candidates so the country filter has something to pick from
_SOURCE = "open-meteo/geocoding"  # cooldown key; see _search()

# A place name's coordinates do not change, so successful lookups are cached
# for the life of the process with no TTL — the same name recurs constantly
# across a session and across users, and every avoided call is quota we keep
# for the forecast/marine endpoints (#106). Only successes are cached: a
# lookup that failed on a transport error must be retried, not remembered.
# Module-level for the same reason as the other adapters' caches — the graph
# builds a fresh Planner/adapter per request.
_CACHE_MAX_ENTRIES = 512
_CACHE_LOCK = threading.Lock()
_GEOCODE_CACHE: dict[tuple[str, str], dict[str, Any]] = {}  # (name, cc) -> location


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _normalise_name(name: str) -> str:
    return " ".join(name.strip().casefold().split())


def _reset_cache() -> None:
    """Test hook (see tests/conftest.py); not used in production code."""
    with _CACHE_LOCK:
        _GEOCODE_CACHE.clear()


class GeocodingAdapter(DataSourceAdapter):
    def __init__(self) -> None:
        s = get_settings()
        self._base = s.geocoding_base_url.rstrip("/")
        self._country_code = (s.geocoding_country_code or "").strip().upper()
        self._api_key = (s.open_meteo_api_key or "").strip()

    def fetch(self, params: dict[str, Any]) -> AdapterResult:
        """params: {"place_name": str}. Returns data
        {"place_name", "lat", "lon", "country_code", "admin1"} or
        status='unavailable'."""
        now = _utcnow()
        raw_name = params.get("place_name")
        if not isinstance(raw_name, str) or not raw_name.strip():
            return AdapterResult(data=None, fetched_at=now, status="unavailable")

        key = (_normalise_name(raw_name), self._country_code)
        with _CACHE_LOCK:
            hit = _GEOCODE_CACHE.get(key)
        if hit is not None:
            return AdapterResult(data=dict(hit), fetched_at=now, status="ok")

        try:
            results = self._search(raw_name)
        except Exception as exc:  # noqa: BLE001 - LLD §2.9: degrade, never raise
            logger.warning("GeocodingAdapter: lookup of %r failed: %s", raw_name, exc)
            return AdapterResult(data=None, fetched_at=now, status="unavailable")

        location = _pick(results, self._country_code)
        if location is None:
            # A real "no such place", not a transport failure. Still
            # unavailable — the Planner must ask, not guess.
            logger.info(
                "GeocodingAdapter: no %s match for %r",
                self._country_code or "global",
                raw_name,
            )
            return AdapterResult(data=None, fetched_at=now, status="unavailable")

        with _CACHE_LOCK:
            if len(_GEOCODE_CACHE) >= _CACHE_MAX_ENTRIES:
                _GEOCODE_CACHE.clear()  # tiny working set in practice; simplest bound
            _GEOCODE_CACHE[key] = location
        return AdapterResult(data=dict(location), fetched_at=now, status="ok")

    def _search(self, name: str) -> list[dict[str, Any]]:
        """Goes through data_access/http_client.py rather than httpx directly
        (#116). This is a third Open-Meteo endpoint on the same metered client
        IP as forecast/marine, and on Render that IP is shared with the rest of
        the node — so a raw httpx.get here retried nothing, respected no
        Retry-After, and kept hammering the geocoding endpoint while the
        weather adapter was already sitting out a 429. Its cooldown key is its
        own (Open-Meteo meters the geocoding API separately from forecast), but
        the mechanism and the budget are shared."""
        params: dict[str, Any] = {
            "name": name,
            "count": _RESULT_COUNT,
            "language": "en",
            "format": "json",
        }
        if self._country_code:
            params["countryCode"] = self._country_code
        if self._api_key:
            params["apikey"] = self._api_key
        r = http_client.get(_SOURCE, f"{self._base}/search", params=params)
        return r.json().get("results") or []


# ---------------------------------------------------------------------- #
# Pure helper (no I/O) — unit-tested directly.
# ---------------------------------------------------------------------- #
def _pick(results: list[dict[str, Any]], country_code: str) -> dict[str, Any] | None:
    """First usable candidate, preferring the requested country.

    The API is already asked to filter by countryCode, so this is belt-and-
    braces: if the filter is ever ignored or the caller searches globally, a
    wrong-country hit must not win silently (see the module docstring on
    "Kochi"). Results arrive population-ranked, so first-match within the
    country is the intended city rather than a same-named village."""
    fallback: dict[str, Any] | None = None
    for r in results:
        lat, lon = _as_float(r.get("latitude")), _as_float(r.get("longitude"))
        if lat is None or lon is None:
            continue
        location = {
            "place_name": r.get("name"),
            "lat": lat,
            "lon": lon,
            "country_code": r.get("country_code"),
            "admin1": r.get("admin1"),
        }
        if not country_code:
            return location
        if (r.get("country_code") or "").upper() == country_code:
            return location
        if fallback is None:
            fallback = location
    # Only reachable when a country filter was requested and nothing matched
    # it; returning None (not `fallback`) is the point — see the docstring.
    return None if country_code else fallback


def _as_float(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        logger.debug("_as_float: non-numeric value %r", v)
        return None
