"""
Ocean Agent — Potential Fishing Zone (PFZ) and oceanographic data.

Owner: P3 (Weather & Ocean Data Engineer).
Implements: FR-OCEAN-1 to FR-OCEAN-4.
Reference: LLD v1.0 §2.4, §4.3 (haversine nearest-PFZ algorithm).

Thin agent over INCOISAdapter: the adapter knows the INCOIS GeoServer shapes
(WFS GeoJSON for PFZ lines, WMS GetFeatureInfo for SST/chl); this module runs
the LLD §4.3 nearest-PFZ maths and maps onto the PFZResult / OceanParams
contracts. No fabricated values on failure (FR-OCEAN-2/4, NFR-REL-1).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from math import asin, atan2, cos, degrees, radians, sin, sqrt

from app.core.config import get_settings
from app.data_access.incois_adapter import INCOISAdapter
from app.schemas.common import LatLon
from app.schemas.ocean import OceanParams, PFZResult

logger = logging.getLogger(__name__)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km — the EXACT formula from LLD §4.3. Do not
    swap in a different approximation: GeofencingAgent's IMBL buffer check
    (LLD §2.5) reuses this function, so results must be consistent across the
    codebase."""
    r = 6371.0  # Earth radius, km
    phi1, phi2 = radians(lat1), radians(lat2)
    d_phi = radians(lat2 - lat1)
    d_lambda = radians(lon2 - lon1)
    a = sin(d_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(d_lambda / 2) ** 2
    return 2 * r * asin(sqrt(a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing from point 1 to point 2, degrees clockwise
    from true north, in [0, 360)."""
    phi1, phi2 = radians(lat1), radians(lat2)
    d_lambda = radians(lon2 - lon1)
    y = sin(d_lambda) * cos(phi2)
    x = cos(phi1) * sin(phi2) - sin(phi1) * cos(phi2) * cos(d_lambda)
    return (degrees(atan2(y, x)) + 360.0) % 360.0


class OceanAgent:
    def __init__(self, adapter: INCOISAdapter) -> None:
        self._adapter = adapter
        self._staleness_hours = get_settings().ocean_pfz_staleness_hours

    def get_nearest_pfz(self, location: LatLon) -> PFZResult | None:
        """Nearest currently-published PFZ advisory line to `location`.

        Deviates from the LLD §2.4 signature (`-> PFZResult`) by returning
        None when INCOIS is unreachable or no advisory is published — the
        orchestration graph already treats a None ocean result as
        'unavailable' (graph._ocean_node, `unavailable=None`), and PFZResult
        has no status field. No fabricated PFZ on failure (NFR-REL-1)."""
        result = self._adapter.fetch({"kind": "pfz"})
        pfz = (result.data or {}).get("pfz") or []
        if result.status == "unavailable" or not pfz:
            logger.info("OceanAgent: no PFZ data for (%s, %s)", location.lat, location.lon)
            return None

        nearest = min(
            pfz,
            key=lambda p: haversine_km(location.lat, location.lon, p["lat"], p["lon"]),
        )
        dist = haversine_km(location.lat, location.lon, nearest["lat"], nearest["lon"])
        brg = bearing_deg(location.lat, location.lon, nearest["lat"], nearest["lon"])

        advisory_ts = _parse_dt((result.data or {}).get("advisory_date"))
        return PFZResult(
            centroid=LatLon(nearest["lat"], nearest["lon"]),
            distance_km=round(dist, 2),
            bearing_deg=round(brg, 1),
            data_timestamp=advisory_ts or result.fetched_at,  # FR-OCEAN-3
            is_stale=self._is_stale(advisory_ts, result.status),  # FR-OCEAN-4
        )

    def get_ocean_parameters(self, location: LatLon) -> OceanParams:
        """SST + chlorophyll where INCOIS publishes them (FR-OCEAN-2).

        Always returns an OceanParams; unpublished / unavailable fields are
        None, never a fabricated or interpolated value."""
        result = self._adapter.fetch(
            {"kind": "ocean_params", "lat": location.lat, "lon": location.lon}
        )
        data = result.data or {}
        if result.status == "unavailable":
            return OceanParams(sea_surface_temp_c=None, chlorophyll_mg_m3=None)

        sst = data.get("sst_c")
        chl = data.get("chlorophyll_mg_m3")
        return OceanParams(
            sea_surface_temp_c=sst,
            chlorophyll_mg_m3=chl,
            # only timestamp a result that actually carries a value
            data_timestamp=result.fetched_at if (sst is not None or chl is not None) else None,
        )

    def _is_stale(self, advisory_ts: datetime | None, adapter_status: str) -> bool:
        if adapter_status == "stale":
            return True
        if advisory_ts is None:
            return False
        age_h = (datetime.now(UTC) - advisory_ts).total_seconds() / 3600.0
        return age_h > self._staleness_hours


def _parse_dt(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except ValueError:
            return None
    return None
