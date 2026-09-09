"""
PFZResult / OceanParams contracts — owned by P3 (Weather & Ocean Data Engineer).

Reference: LLD v1.0 §2.4, §4.3, implements FR-OCEAN-1 to FR-OCEAN-4.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.schemas.common import LatLon


@dataclass
class PFZResult:
    centroid: LatLon
    distance_km: float
    bearing_deg: float
    data_timestamp: datetime
    is_stale: bool  # FR-OCEAN-4 — flag data older than a configurable threshold


@dataclass
class NearbyPFZ:
    """Issue #174: several PFZ centroids near the queried point, nearest
    first — the answer to a "which zones" query, as opposed to PFZResult
    (the single nearest one). Descriptive only: not consumed by the
    Risk/Safety Figure 2 tree, only by Synthesis.

    `zones` may be empty (feed reachable, but nothing published within
    `radius_km`); that is distinct from the method returning None (feed
    unavailable). Synthesis must not present an empty list as "an area was
    surveyed and is clear"."""

    zones: list[PFZResult]
    radius_km: float
    data_timestamp: datetime | None
    is_stale: bool


@dataclass
class OceanParams:
    """SST / chlorophyll for the queried region.

    FR-OCEAN-2: return None for any field not published for the region — do not
    substitute a fabricated or interpolated value (same principle as WeatherResult
    .status == 'unavailable').
    """

    sea_surface_temp_c: float | None
    chlorophyll_mg_m3: float | None
    data_timestamp: datetime | None = None

    # TODO(P3): confirm which fields INCOIS actually publishes per-region and
    # whether a staleness threshold analogous to FR-OCEAN-4 applies here too.
