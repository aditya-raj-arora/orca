"""
INCOISAdapter — the ONLY module permitted to know INCOIS's specific data
access shapes. Confirmed sources (docs/p3-data-source-spike.md §3), all keyless:

  * PFZ advisory geometry (FR-OCEAN-1/3): GeoServer WFS GetFeature -> GeoJSON,
    typeName PFZ_Automation:pfzlines. ~96 MultiLineString advisory lines,
    coords [lon, lat] WGS84, dated via Year + Julian_day. Fetched whole and
    reduced to one centroid per line.
  * SST + chlorophyll (FR-OCEAN-2): GeoServer WMS GetFeatureInfo, INFO_FORMAT
    application/json, on the PFZ-TUNA-SST-CHL workspace (layers sst, chl).
    GRAY_INDEX carries the value; -1 / large-negative / null all mean "no data
    here" -> None, never a fabricated value.

Owner: P3 (Weather & Ocean Data Engineer).
Reference: LLD v1.0 §2.9.

Contract (LLD §2.9): fetch() NEVER raises. On any transport failure it returns
AdapterResult(status='unavailable'). A successfully fetched but old PFZ
advisory returns status='stale' (data still included).
"""
from __future__ import annotations

import gzip
import json
import logging
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from app.core.config import get_settings
from app.data_access.base import AdapterResult, DataSourceAdapter

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT_S = 5.0
_PFZ_TYPENAME = "PFZ_Automation:pfzlines"
# WMS GetFeatureInfo no-data: INCOIS coverages use -1; guard large negatives too.
_NODATA_THRESHOLD = -900.0

# Per-day PFZ cache (P1 contract-lock decision, 2026-09-01): the WFS pull is a
# single ~1.3 MB GeoJSON response against a 6s AGENT_TIMEOUT_SECONDS budget
# (orchestration/graph.py), and PFZ advisories are published once per day
# (Year + Julian_day), so re-fetching within the same UTC day buys nothing.
# Module-level (not instance-level): build_orchestration_graph() constructs a
# fresh INCOISAdapter per request (graph.py's run_query TODO(P1) notes this),
# so an instance attribute would never actually hit. Only successful fetches
# are cached — a transient failure must not get "stuck" unavailable/stale for
# the rest of the day.
_PFZ_CACHE_LOCK = threading.Lock()
_PFZ_CACHE: dict[str, AdapterResult] = {}  # "YYYY-MM-DD" (UTC) -> cached result


def _utcnow() -> datetime:
    return datetime.now(UTC)


class INCOISAdapter(DataSourceAdapter):
    def __init__(self) -> None:
        s = get_settings()
        self._pfz_wfs_url = s.incois_pfz_wfs_url.rstrip("/")
        self._geoserver_url = s.incois_geoserver_url.rstrip("/")
        self._staleness_hours = s.ocean_pfz_staleness_hours
        self._snapshot_path = s.incois_pfz_snapshot_path

    # ------------------------------------------------------------------ #
    def fetch(self, params: dict[str, Any]) -> AdapterResult:
        kind = params.get("kind", "pfz")
        try:
            if kind == "pfz":
                return self._fetch_pfz()
            if kind == "ocean_params":
                return self._fetch_ocean_params(
                    float(params["lat"]), float(params["lon"])
                )
        except Exception as exc:  # noqa: BLE001 - LLD §2.9: degrade, never raise
            logger.warning("INCOISAdapter.fetch(%s) failed: %s", kind, exc)
            return AdapterResult(data=None, fetched_at=_utcnow(), status="unavailable")
        logger.warning("INCOISAdapter.fetch: unknown kind %r", kind)
        return AdapterResult(data=None, fetched_at=_utcnow(), status="unavailable")

    # ------------------------------------------------------------------ #
    # PFZ advisory geometry
    # ------------------------------------------------------------------ #
    def _fetch_pfz(self) -> AdapterResult:
        now = _utcnow()
        cache_key = now.strftime("%Y-%m-%d")
        with _PFZ_CACHE_LOCK:
            cached = _PFZ_CACHE.get(cache_key)
        if cached is not None:
            return cached

        try:
            gj = self._get_pfz_geojson()
            centroids, advisory_date = _parse_pfz_geojson(gj)
        except Exception as exc:  # noqa: BLE001 - LLD §2.9: degrade, never raise
            logger.warning("INCOISAdapter: live PFZ fetch/parse failed (%s)", exc)
            centroids, advisory_date = [], None

        if not centroids:
            # Live feed unusable. Try the bundled snapshot (#38 / HLD §9); if
            # that's absent too, report unavailable. Neither path is cached —
            # a transient outage must not stick for the rest of the day.
            return self._pfz_snapshot_fallback(now)

        result = AdapterResult(
            data=_pfz_data(centroids, advisory_date),
            fetched_at=now,
            status=_pfz_status(now, advisory_date, self._staleness_hours),  # type: ignore[arg-type]
        )
        with _PFZ_CACHE_LOCK:
            _PFZ_CACHE.clear()  # single-entry cache: only today's key is ever useful
            _PFZ_CACHE[cache_key] = result
        return result

    def _pfz_snapshot_fallback(self, now: datetime) -> AdapterResult:
        snap = _load_pfz_snapshot(self._snapshot_path)
        if snap is None:
            return AdapterResult(data=None, fetched_at=now, status="unavailable")
        centroids, advisory_date = snap
        logger.warning(
            "INCOISAdapter: live PFZ unavailable — serving bundled snapshot as "
            "stale (%d zones, advisory %s)",
            len(centroids),
            advisory_date.date() if advisory_date else "unknown",
        )
        return AdapterResult(
            data=_pfz_data(centroids, advisory_date), fetched_at=now, status="stale"
        )

    def _get_pfz_geojson(self) -> dict[str, Any]:
        r = httpx.get(
            self._pfz_wfs_url,
            params={
                "service": "WFS",
                "version": "1.1.0",
                "request": "GetFeature",
                "typeName": _PFZ_TYPENAME,
                "outputFormat": "application/json",
            },
            timeout=_HTTP_TIMEOUT_S,
        )
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------ #
    # SST / chlorophyll
    # ------------------------------------------------------------------ #
    def _fetch_ocean_params(self, lat: float, lon: float) -> AdapterResult:
        now = _utcnow()
        sst = self._get_gray_index("sst", lat, lon)
        chl = self._get_gray_index("chl", lat, lon)
        return AdapterResult(
            data={"sst_c": sst, "chlorophyll_mg_m3": chl},
            fetched_at=now,
            status="ok",
        )

    def _get_gray_index(self, layer: str, lat: float, lon: float) -> float | None:
        """One WMS GetFeatureInfo call -> the pixel value at (lat, lon), or None
        for the no-data sentinels."""
        d = 0.25  # half-degree bbox around the point; query its centre pixel
        r = httpx.get(
            f"{self._geoserver_url}/wms",
            params={
                "service": "WMS",
                "version": "1.1.1",
                "request": "GetFeatureInfo",
                "layers": layer,
                "query_layers": layer,
                "info_format": "application/json",
                "srs": "EPSG:4326",
                "bbox": f"{lon - d},{lat - d},{lon + d},{lat + d}",
                "width": 256,
                "height": 256,
                "x": 128,
                "y": 128,
            },
            timeout=_HTTP_TIMEOUT_S,
        )
        r.raise_for_status()
        feats = r.json().get("features") or []
        if not feats:
            return None
        return _gray_index_value(feats[0].get("properties", {}).get("GRAY_INDEX"))


# ---------------------------------------------------------------------- #
# Pure helpers (no I/O) — unit-tested against the captured samples.
# ---------------------------------------------------------------------- #
def _gray_index_value(raw: Any) -> float | None:
    """Map a WMS GetFeatureInfo GRAY_INDEX to a real value or None.
    None / -1 / any large negative == 'no data at this pixel/date' (FR-OCEAN-4 /
    NFR-REL-1: never a fabricated value)."""
    if raw is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        logger.debug("_gray_index_value: non-numeric GRAY_INDEX %r", raw)
        return None
    if v == -1.0 or v <= _NODATA_THRESHOLD:
        return None
    return v


def _iter_coords(geometry: dict[str, Any]) -> list[list[float]]:
    """Flatten a (Multi)LineString / (Multi)Point geometry to a list of
    [lon, lat] pairs; [] if the geometry has no usable coordinate list (e.g. a
    summarised sample feature)."""
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if not isinstance(coords, list):
        return []
    if gtype == "Point":
        return [coords] if len(coords) >= 2 else []
    if gtype in ("LineString", "MultiPoint"):
        return [p for p in coords if isinstance(p, list) and len(p) >= 2]
    if gtype == "MultiLineString":
        return [p for line in coords for p in line if isinstance(p, list) and len(p) >= 2]
    return []


def _parse_pfz_geojson(gj: dict[str, Any]) -> tuple[list[dict[str, Any]], datetime | None]:
    """GeoJSON FeatureCollection -> (list of {lat, lon, uid, category}, advisory_date).
    Each advisory line is reduced to the mean of its vertices (LLD §4.3 works
    on PFZ centroids)."""
    centroids: list[dict[str, Any]] = []
    advisory_date: datetime | None = None
    for feat in gj.get("features") or []:
        pts = _iter_coords(feat.get("geometry") or {})
        if not pts:
            continue
        lon = sum(p[0] for p in pts) / len(pts)
        lat = sum(p[1] for p in pts) / len(pts)
        props = feat.get("properties") or {}
        centroids.append(
            {
                "lat": lat,
                "lon": lon,
                "uid": props.get("UID"),
                "category": props.get("Category"),
            }
        )
        if advisory_date is None:
            advisory_date = _advisory_date_from_props(props)
    return centroids, advisory_date


def _advisory_date_from_props(props: dict[str, Any]) -> datetime | None:
    """PFZ features carry Year (int) + Julian_day (day-of-year string)."""
    try:
        year = int(props["Year"])
        jd = int(props["Julian_day"])
    except (KeyError, TypeError, ValueError) as exc:
        logger.debug("_advisory_date_from_props: missing/bad Year or Julian_day (%s)", exc)
        return None
    if not (1 <= jd <= 366):
        return None
    return datetime(year, 1, 1, tzinfo=UTC) + timedelta(days=jd - 1)


def _pfz_data(centroids: list[dict[str, Any]], advisory_date: datetime | None) -> dict[str, Any]:
    return {
        "pfz": centroids,
        "advisory_date": advisory_date.isoformat() if advisory_date else None,
        "count": len(centroids),
    }


def _pfz_status(now: datetime, advisory_date: datetime | None, staleness_hours: float) -> str:
    if advisory_date is None:
        return "ok"
    age_h = (now - advisory_date).total_seconds() / 3600.0
    return "stale" if age_h > staleness_hours else "ok"


def _load_pfz_snapshot(
    path: str,
) -> tuple[list[dict[str, Any]], datetime | None] | None:
    """Read the bundled PFZ snapshot (optionally gzipped). Returns
    (centroids, advisory_date) or None if the file is missing / unreadable /
    empty — the caller then reports unavailable (never a fabricated value)."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        raw = p.read_bytes()
        if p.suffix == ".gz":
            raw = gzip.decompress(raw)
        gj = json.loads(raw)
    except (OSError, ValueError, gzip.BadGzipFile) as exc:
        logger.warning("INCOISAdapter: PFZ snapshot at %s unreadable (%s)", path, exc)
        return None
    centroids, advisory_date = _parse_pfz_geojson(gj)
    return (centroids, advisory_date) if centroids else None
