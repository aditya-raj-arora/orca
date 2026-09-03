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

import threading

from shapely.geometry import Point, shape
from shapely.ops import nearest_points, unary_union

from app.agents.ocean_agent import haversine_km
from app.core.config import get_settings
from app.data_access.gis_boundary_adapter import GISBoundaryAdapter
from app.schemas.common import LatLon
from app.schemas.geofence import GeofenceResult


class GeofencingAgent:
    def __init__(self, adapter: GISBoundaryAdapter) -> None:
        self._adapter = adapter
        # FR-GEO: buffer threshold is configurable, not hardcoded (LLD §2.5).
        self._imbl_buffer_km = get_settings().imbl_buffer_km

        # Parsed-geometry cache: boundary GeoJSON is reference data (slow-
        # changing, per HLD §4.1), same rationale as GISBoundaryAdapter's own
        # in-process cache — build the Shapely geometries once, not per call.
        self._geometry_lock = threading.Lock()
        self._imbl_boundary = None  # Shapely (Multi)LineString — the IMBL line itself
        self._mpas: list[tuple[str | None, object]] | None = None  # (name, polygon) pairs

    def check(self, location: LatLon) -> GeofenceResult:
        """FR-GEO-1/2: MPA point-in-polygon test + IMBL buffer distance.

        Raises on unavailable/malformed boundary data rather than returning a
        degraded GeofenceResult (there is no "unavailable" status on this
        contract — see schemas/geofence.py) — graph.py's _call_bounded
        catches this and substitutes `unavailable=None`, which
        RiskSafetyAgent already treats as INSUFFICIENT_DATA (LLD §6).
        """
        self._ensure_geometry_loaded()
        point = Point(location.lon, location.lat)  # Shapely is (x=lon, y=lat)

        mpa_name = None
        for name, polygon in self._mpas:
            if polygon.covers(point):  # interior or boundary counts as "within"
                mpa_name = name
                break

        nearest_on_imbl, _ = nearest_points(self._imbl_boundary, point)
        # FR-GEO-2: haversine_km, not the raw (unprojected) Shapely distance —
        # LLD §4.3 requires the same great-circle formula everywhere in the
        # codebase (see ocean_agent.haversine_km docstring).
        imbl_distance_km = haversine_km(
            location.lat, location.lon, nearest_on_imbl.y, nearest_on_imbl.x
        )

        return GeofenceResult(
            within_imbl_buffer=imbl_distance_km <= self._imbl_buffer_km,
            imbl_distance_km=imbl_distance_km,
            within_mpa=mpa_name is not None,
            mpa_name=mpa_name,
        )

    def _ensure_geometry_loaded(self) -> None:
        with self._geometry_lock:
            if self._imbl_boundary is not None and self._mpas is not None:
                return

            result = self._adapter.fetch({})
            if result.status != "ok" or not result.data:
                raise RuntimeError(
                    "GeofencingAgent: GIS boundary data unavailable "
                    f"(adapter status={result.status!r})"
                )

            boundaries = result.data.get("boundaries", [])
            imbl_geoms = [
                shape(b["geometry"]) for b in boundaries if b.get("type") == "IMBL"
            ]
            mpas = [
                (b.get("name"), shape(b["geometry"]))
                for b in boundaries
                if b.get("type") == "MPA"
            ]

            if not imbl_geoms:
                raise RuntimeError(
                    "GeofencingAgent: no IMBL feature in loaded boundary data"
                )

            # The IMBL is published as the EEZ boundary polygon (see the
            # bundled GeoJSON), but FR-GEO-2 is a proximity-to-the-line check,
            # not containment — take the polygon's boundary (its rings) as
            # the line to measure distance against. unary_union first in case
            # of multiple IMBL features/parts.
            self._imbl_boundary = unary_union(imbl_geoms).boundary
            self._mpas = mpas
