"""
GISBoundaryAdapter — loads/serves IMBL and MPA boundary geometry.

Owner: P4 (Geospatial & Risk Engineer).
Reference: LLD v1.0 §2.9, §3 (geofence_boundary table).

SRS RISK-4: "Public GIS boundary data (IMBL, MPA) format/availability has not
yet been finalised... Confirm and download a working boundary dataset on Day 1,
before agent development begins." Do this first — GeofencingAgent is blocked
without it.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import psycopg

from app.core.config import get_settings
from app.data_access.base import AdapterResult, DataSourceAdapter


def _sync_url(async_url: str) -> str:
    return async_url.replace("postgresql+asyncpg://", "postgresql://")


class GISBoundaryAdapter(DataSourceAdapter):
    def __init__(self) -> None:
        self._db_url = _sync_url(get_settings().database_url)

    def fetch(self, params: dict[str, Any]) -> AdapterResult:
        boundary_type = params.get("type")
        try:
            with psycopg.connect(self._db_url) as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT name, ST_AsGeoJSON(geometry) FROM geofence_boundary WHERE type = %s",
                    (boundary_type,),
                )
                rows = cur.fetchall()
        except Exception:
            return AdapterResult(data=None, fetched_at=self._now(), status="unavailable")

        features = [{"name": name, "geometry": geojson} for name, geojson in rows]
        return AdapterResult(data={"features": features}, fetched_at=self._now(), status="ok")

    def _now(self) -> datetime:
        return datetime.now(UTC)