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

    # TODO(P3): populate visibility / precipitation fields per FR-WX-1 — the LLD
    # dataclass lists "wind, wave height, precipitation, visibility" in prose
    # (LLD §2.3) but only wind/wave/alerts are given explicit fields; confirm
    # final field set against the actual weather provider's response shape
    # (SRS §6.4 dependency) before Sprint 1 exit.
