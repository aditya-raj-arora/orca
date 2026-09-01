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


@dataclass
class GeofenceResult:
    within_imbl_buffer: bool
    imbl_distance_km: float
    within_mpa: bool
    mpa_name: str | None = None

    # TODO(P4): the buffer threshold (default 5 km per LLD §2.5) must be read
    # from IMBL_BUFFER_KM (see src/backend/.env.example) — not hardcoded here
    # or in geofencing_agent.py.
