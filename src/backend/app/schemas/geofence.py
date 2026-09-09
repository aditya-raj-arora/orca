"""
GeofenceResult contract — owned by P4 (Geospatial & Risk Engineer).

Reference: LLD v1.0 §2.5, implements FR-GEO-1 to FR-GEO-4.

IMPORTANT (FR-GEO-4): this result must NEVER be suppressed or downgraded by any
other agent's output. The Risk/Safety Agent (LLD §4.2 / Figure 2) treats a
geofence violation as non-negotiable. Do not add a "confidence" or "override"
field here that would let a caller soften this — that would violate FR-GEO-4.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class GeofenceResult:
    within_imbl_buffer: bool
    imbl_distance_km: float
    within_mpa: bool
    mpa_name: str | None = None


@dataclass
class NearbyMPA:
    name: str | None
    distance_km: float  # 0.0 when the queried point is inside this MPA
    contains_point: bool


@dataclass
class NearbyZones:
    """Issue #174: MPAs within a search radius of the queried point (plus any
    MPA the point is already inside), nearest first — the answer to a "which
    zones should be avoided" query.

    Enumerative companion to GeofenceResult, NOT a replacement: GeofenceResult
    stays the non-negotiable point check the Risk/Safety Agent consumes
    (FR-GEO-4). Like GeofenceResult, this carries no confidence/override field
    that would let a caller soften a proximity finding. `mpas` may be empty
    (source reachable, nothing within radius); a None return means the source
    was unavailable."""

    mpas: list[NearbyMPA]
    radius_km: float
    data_timestamp: datetime

    # TODO(P4): the buffer threshold (default 5 km per LLD §2.5) must be read
    # from IMBL_BUFFER_KM (see src/backend/.env.example) — not hardcoded here
    # or in geofencing_agent.py.
