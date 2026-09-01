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

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.core.config import get_settings
from app.data_access.base import AdapterResult, DataSourceAdapter

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT_S = 5.0
_PFZ_TYPENAME = "PFZ_Automation:pfzlines"
# WMS GetFeatureInfo no-data: INCOIS coverages use -1; guard large negatives too.
_NODATA_THRESHOLD = -900.0


def _utcnow() -> datetime:
    return datetime.now(UTC)


class INCOISAdapter(DataSourceAdapter):
    def __init__(self) -> None:
        s = get_settings()
        self._pfz_wfs_url = s.incois_pfz_wfs_url.rstrip("/")
        self._geoserver_url = s.incois_geoserver_url.rstrip("/")
        self._staleness_hours = s.ocean_pfz_staleness_hours

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
        gj = self._get_pfz_geojson()
        centroids, advisory_date = _parse_pfz_geojson(gj)
        if not centroids:
            return AdapterResult(data=None, fetched_at=now, status="unavailable")

        status: str = "ok"
        if advisory_date is not None:
            age_h = (now - advisory_date).total_seconds() / 3600.0
            if age_h > self._staleness_hours:
                status = "stale"

        return AdapterResult(
            data={
                "pfz": centroids,
                "advisory_date": advisory_date.isoformat() if advisory_date else None,
                "count": len(centroids),
            },
            fetched_at=now,
            status=status,  # type: ignore[arg-type]
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
    except (KeyError, TypeError, ValueError):
        return None
    if not (1 <= jd <= 366):
        return None
    return datetime(year, 1, 1, tzinfo=UTC) + timedelta(days=jd - 1)
