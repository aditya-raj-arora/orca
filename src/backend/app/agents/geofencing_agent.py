"""
Geofencing Agent — IMBL proximity and MPA boundary checks.

Owner: P4 (Geospatial & Risk Engineer).
Implements: FR-GEO-1 to FR-GEO-4.
Reference: LLD v1.0 §2.5.

FR-GEO-4 (do not violate): this agent's output must never be suppressed or
downgraded by any other agent. Callers (Risk/Safety Agent, LLD §4.2/Figure 2)
must treat within_mpa=True or within_imbl_buffer=True as non-negotiable.
"""
from __future__ import annotations

from app.core.config import get_settings
from app.data_access.gis_boundary_adapter import GISBoundaryAdapter
from app.schemas.common import LatLon
from app.schemas.geofence import GeofenceResult


class GeofencingAgent:
    def __init__(self, adapter: GISBoundaryAdapter) -> None:
        self._adapter = adapter
        # FR-GEO: buffer threshold is configurable, not hardcoded (LLD §2.5).
        self._imbl_buffer_km = get_settings().imbl_buffer_km

    def check(self, location: LatLon) -> GeofenceResult:
        """
        TODO(P4):
          - MPA check: point-in-polygon test using Shapely against cached
            GeofenceBoundary geometry (fetched via self._adapter, ultimately
            backed by the geofence_boundary table in app/db/schema.sql).
          - IMBL check: haversine distance (see app/agents/ocean_agent.py
            haversine_km — reuse it, don't reimplement) against the IMBL
            polyline, compared to self._imbl_buffer_km.
          - Generate a distinct, unambiguous alert on violation (FR-GEO-3) —
            this alert flows into the Risk/Safety Agent's rationale (FR-RISK-2).
        """
        raise NotImplementedError
