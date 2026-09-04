"""
WeatherResult contract — owned by P3 (Weather & Ocean Data Engineer), locked
after Day 1 contract-lock sync (CONTRIBUTING.md §6).

Reference: LLD v1.0 §2.3, implements FR-WX-1 to FR-WX-4.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.schemas.common import AgentStatus


@dataclass
class WeatherResult:
    wind_speed_kmh: float
    wave_height_m: float
    active_alerts: list[str] = field(default_factory=list)
    data_timestamp: datetime | None = None
    # FR-WX-4: on adapter failure, return status='unavailable' — never fabricate
    # a value. NFR-REL-1/2 and the Risk/Safety Agent (LLD §4.2) both depend on
    # this being honest.
    status: AgentStatus = "ok"

    # FR-WX-1: precipitation / visibility, resolved at the P1 contract-lock
    # sync (config/graph coordination, 2026-09-01) — the adapter has always
    # carried these in its normalised dict (weather_adapter.py's
    # precipitation_mm / visibility_m keys); they just weren't reaching this
    # dataclass. None means the provider didn't report the field (same "never
    # fabricate" rule as everything else here), not "zero".
    precipitation_mm: float | None = None
    visibility_m: float | None = None

    # NFR-REL-2: True means both alert sources (WeatherAPI + GDACS) were
    # actually reachable — active_alerts == [] can be trusted as "no active
    # alerts". False means neither could be checked, so active_alerts == []
    # must NOT be read as "clear": it means "unknown". The Risk/Safety Agent
    # (P4, #33) MUST treat False identically to a missing input — verdict
    # INSUFFICIENT_DATA, never SAFE — per its own NFR-REL-2 rule; it must not
    # fall through to the weather-alerts branch of the decision tree as if
    # active_alerts were authoritative.
    alerts_source_available: bool = True
