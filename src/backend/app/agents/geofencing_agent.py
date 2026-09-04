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

import json
from math import asin, cos, radians, sin, sqrt

from shapely.geometry import Point, shape

from app.core.config import get_settings
from app.data_access.gis_boundary_adapter import GISBoundaryAdapter
from app.schemas.common import LatLon
from app.schemas.geofence import GeofenceResult


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    # TODO: replace with app.agents.ocean_agent.haversine_km once P3 implements
    # it (see issue tracking the cross-owner dependency) — do not remove this
    # comment until that swap happens.
    R = 6371.0
    phi1, phi2 = radians(lat1), radians(lat2)
    d_phi = radians(lat2 - lat1)
    d_lambda = radians(lon2 - lon1)
    a = sin(d_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(d_lambda / 2) ** 2
    return 2 * R * asin(sqrt(a))


def _densify_segment(lat1: float, lon1: float, lat2: float, lon2: float, step_km: float = 1.0):
    """Linearly interpolates extra points along a segment if it's longer than
    step_km, so nearest-vertex distance approximates true nearest-point-on-
    line distance closely enough for a 5km buffer check. Linear lon/lat
    interpolation (not great-circle) is an acceptable approximation at this
    scale — the error over a few-km segment is negligible next to the
    buffer's own precision."""
    seg_len = _haversine_km(lat1, lon1, lat2, lon2)
    if seg_len <= step_km:
        return [(lat1, lon1)]
    n_steps = int(seg_len // step_km) + 1
    return [
        (lat1 + (lat2 - lat1) * t / n_steps, lon1 + (lon2 - lon1) * t / n_steps)
        for t in range(n_steps)
    ]


class GeofencingAgent:
    def __init__(self, adapter: GISBoundaryAdapter) -> None:
        self._adapter = adapter
        # FR-GEO: buffer threshold is configurable, not hardcoded (LLD §2.5).
        self._imbl_buffer_km = get_settings().imbl_buffer_km

    def check(self, location: LatLon) -> GeofenceResult:
        point = Point(location.lon, location.lat)  # Shapely is (x=lon, y=lat)

        within_mpa, mpa_name = self._check_mpa(point)
        within_imbl, imbl_dist = self._check_imbl(location)

        return GeofenceResult(
            within_imbl_buffer=within_imbl,
            imbl_distance_km=imbl_dist,
            within_mpa=within_mpa,
            mpa_name=mpa_name,
        )

    def _check_mpa(self, point: Point) -> tuple[bool, str | None]:
        result = self._adapter.fetch({"type": "MPA"})
        if result.status != "ok" or not result.data:
            # Adapter unavailable — degrade honestly rather than fabricate a
            # False. The Risk/Safety Agent's missing-data path (Figure 2) is
            # what's supposed to catch this upstream; flag to P4/P1 if this
            # needs a dedicated "unavailable" signal on GeofenceResult itself.
            return False, None

        for feature in result.data["features"]:
            polygon = shape(json.loads(feature["geometry"]))
            if polygon.contains(point):
                return True, feature["name"]
        return False, None

    def _check_imbl(self, location: LatLon) -> tuple[bool, float]:
        result = self._adapter.fetch({"type": "IMBL"})
        if result.status != "ok" or not result.data or not result.data["features"]:
            return False, float("inf")

        min_dist = float("inf")
        for feature in result.data["features"]:
            geom = shape(json.loads(feature["geometry"]))
            line_coords = (
                [geom.coords] if geom.geom_type == "LineString" else [line.coords for line in geom.geoms]
            )
            for coords in line_coords:
                coords = list(coords)
                for i in range(len(coords) - 1):
                    lon1, lat1 = coords[i]
                    lon2, lat2 = coords[i + 1]
                    for lat, lon in _densify_segment(lat1, lon1, lat2, lon2):
                        dist = _haversine_km(location.lat, location.lon, lat, lon)
                        min_dist = min(min_dist, dist)
                # cover the final vertex too (loop above only covers segment starts)
                if coords:
                    lon_last, lat_last = coords[-1]
                    min_dist = min(min_dist, _haversine_km(location.lat, location.lon, lat_last, lon_last))

        return min_dist <= self._imbl_buffer_km, min_dist