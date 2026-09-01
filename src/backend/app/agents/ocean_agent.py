"""
Ocean Agent — Potential Fishing Zone (PFZ) and oceanographic data.

Owner: P3 (Weather & Ocean Data Engineer).
Implements: FR-OCEAN-1 to FR-OCEAN-4.
Reference: LLD v1.0 §2.4, §4.3 (haversine nearest-PFZ algorithm).
"""
from __future__ import annotations

from app.data_access.incois_adapter import INCOISAdapter
from app.schemas.common import LatLon
from app.schemas.ocean import OceanParams, PFZResult


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km. Exact formula specified in LLD §4.3 — do
    not substitute a different approximation, other modules (Geofencing IMBL
    buffer check, LLD §2.5) reuse the same formula and results should be
    consistent across the codebase.

    TODO(P3): implement per LLD §4.3:
        R = 6371.0
        phi1, phi2 = radians(lat1), radians(lat2)
        d_phi = radians(lat2 - lat1)
        d_lambda = radians(lon2 - lon1)
        a = sin(d_phi/2)**2 + cos(phi1)*cos(phi2)*sin(d_lambda/2)**2
        return 2 * R * asin(sqrt(a))
    """
    raise NotImplementedError


class OceanAgent:
    def __init__(self, adapter: INCOISAdapter) -> None:
        self._adapter = adapter

    def get_nearest_pfz(self, location: LatLon) -> PFZResult:
        """Uses haversine_km against all currently published PFZ centroids
        (fetched via self._adapter) to find the minimum-distance zone.

        TODO(P3):
          - Fetch all current PFZ centroids from INCOISAdapter.
          - Compute (pfz, haversine_km(...)) for each, take the min.
          - Compute bearing_deg from location to the nearest centroid.
          - Set is_stale per FR-OCEAN-4 (configurable staleness threshold —
            add a setting, don't hardcode).
        """
        raise NotImplementedError

    def get_ocean_parameters(self, location: LatLon) -> OceanParams:
        """FR-OCEAN-2: SST + chlorophyll where INCOIS publishes them.

        TODO(P3): return None fields (not fabricated values) for anything not
        published for the queried region — same honesty principle as
        WeatherResult.status.
        """
        raise NotImplementedError
