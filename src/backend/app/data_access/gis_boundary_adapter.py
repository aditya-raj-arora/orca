"""
GISBoundaryAdapter — loads/serves IMBL and MPA boundary geometry.

Owner: P4 (Geospatial & Risk Engineer).
Reference: LLD v1.0 §2.9, §3 (geofence_boundary table).

SRS RISK-4: "Public GIS boundary data (IMBL, MPA) format/availability has not
yet been finalised... Confirm and download a working boundary dataset on Day 1,
before agent development begins." Do this first — GeofencingAgent is blocked
without it.

Data source (issue #4 / docs/p4-data-source-spike.md):
  - IMBL: Indian Exclusive Economic Zone (200 NM), Marine Regions (VLIZ)
    Maritime Boundaries Geodatabase v12, MRGID 8480. Keyless WFS GeoJSON.
  - MPA: WDPA/Protected Planet marine + coastal protected areas for India
    (REALM in {"Marine", "Coastal"}), from the monthly WDPA_WDOECM country
    package (no API token needed for the bulk country download).
  Both bundled as WGS84 (EPSG:4326) GeoJSON at GIS_BOUNDARY_DATA_PATH to match
  geofence_boundary.geometry's GEOMETRY(Geometry, 4326) column (LLD §3).
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.data_access.base import AdapterResult, DataSourceAdapter

logger = logging.getLogger(__name__)

# This is reference data (slow-changing, per HLD §4.1 entity notes): a single
# in-process cache with no background refresh is fine for the prototype
# (mirrors INCOISAdapter's per-request-instance cache pattern, LLD §2.9).
_CACHE_LOCK = threading.Lock()
_CACHE: AdapterResult | None = None


def _sync_url(async_url: str) -> str:
    return async_url.replace("postgresql+asyncpg://", "postgresql://")


class GISBoundaryAdapter(DataSourceAdapter):
    def __init__(self) -> None:
        self._data_path = Path(get_settings().gis_boundary_data_path)

    def fetch(self, params: dict[str, Any]) -> AdapterResult:
        """Load IMBL + MPA boundary features from the GeoJSON at
        self._data_path, caching the parsed result in-process.

        Same error contract as the other adapters (LLD §2.9): return
        status='unavailable' rather than raising if the data can't be loaded,
        so GeofencingAgent degrades per the LLD §6 error table.
        """
        global _CACHE
        with _CACHE_LOCK:
            if _CACHE is not None:
                return _CACHE
            try:
                result = self._load_from_disk()
            except Exception as exc:  # noqa: BLE001 - LLD §2.9: degrade, never raise
                logger.warning(
                    "GISBoundaryAdapter.fetch: failed to load %s: %s",
                    self._data_path,
                    exc,
                )
                return AdapterResult(data=None, fetched_at=self._now(), status="unavailable")
            _CACHE = result
            return result

    def _load_from_disk(self) -> AdapterResult:
        with self._data_path.open("r", encoding="utf-8") as f:
            geojson = json.load(f)

        features = geojson.get("features", [])
        boundaries: list[dict[str, Any]] = []
        for feature in features:
            props = feature.get("properties", {})
            geometry = feature.get("geometry")
            boundary_type = props.get("type")
            if boundary_type not in ("IMBL", "MPA") or geometry is None:
                logger.warning(
                    "GISBoundaryAdapter: skipping malformed feature (type=%r)",
                    boundary_type,
                )
                continue
            boundaries.append(
                {
                    "type": boundary_type,
                    "name": props.get("name"),
                    "geometry": geometry,
                    "source": props.get("source"),
                }
            )

        if not any(b["type"] == "IMBL" for b in boundaries) or not any(
            b["type"] == "MPA" for b in boundaries
        ):
            raise ValueError(
                f"{self._data_path} must contain at least one IMBL and one "
                "MPA feature (acceptance criteria, issue #4)"
            )

        return AdapterResult(
            data={"boundaries": boundaries}, fetched_at=self._now(), status="ok"
        )

    def _now(self) -> datetime:
        return datetime.now(UTC)