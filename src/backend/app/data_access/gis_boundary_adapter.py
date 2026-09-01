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

from datetime import datetime, timezone
from typing import Any

from app.core.config import get_settings
from app.data_access.base import AdapterResult, DataSourceAdapter


class GISBoundaryAdapter(DataSourceAdapter):
    def __init__(self) -> None:
        self._data_path = get_settings().gis_boundary_data_path

    def fetch(self, params: dict[str, Any]) -> AdapterResult:
        """
        TODO(P4):
          - Load boundary geometry either from the geofence_boundary table
            (app/db/schema.sql) if already seeded, or directly from the
            GeoJSON at self._data_path on first run / cold cache.
          - This is reference data (slow-changing, per HLD §4.1 entity notes)
            — a simple in-process cache with manual refresh is fine for the
            prototype; no need for a background refresh job in this scope.
          - Same error contract as the other adapters: return
            status='unavailable' rather than raising if the data can't be
            loaded, so GeofencingAgent degrades per the LLD §6 error table.
        """
        raise NotImplementedError

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)
