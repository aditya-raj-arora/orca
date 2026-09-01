"""
Location payload coercion shared by WeatherAgent and OceanAgent.

The orchestration graph currently hands each specialist agent whatever the
Planner's ExecutionPlan carries in `input_payload["location"]` — a plain
`{"place_name", "lat", "lon"}` dict (LLD §2.2 / planner_agent.as_location_dict)
— while the agent signatures (LLD §2.3-2.5) are typed `LatLon`. Until
graph._location_for()'s `TODO(P1)` conversion lands, accept both shapes so the
weather / ocean vertical slice can be integrated and tested end to end.

Owner: P3.
"""
from __future__ import annotations

from typing import Any

from app.schemas.common import LatLon


def as_latlon(value: Any) -> LatLon | None:
    """Coerce a `LatLon`, a `{"lat": ..., "lon": ...}` mapping, or any object
    with `.lat` / `.lon` into a `LatLon`. Returns None if no usable coordinate
    pair is present — the caller then reports 'unavailable' / None rather than
    guessing a location (FR-WX-4 / NFR-REL-1)."""
    if isinstance(value, LatLon):
        return value
    if isinstance(value, dict):
        lat, lon = value.get("lat"), value.get("lon")
    else:
        lat, lon = getattr(value, "lat", None), getattr(value, "lon", None)
    if lat is None or lon is None:
        return None
    try:
        return LatLon(float(lat), float(lon))
    except (TypeError, ValueError):
        return None
