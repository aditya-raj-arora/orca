"""
Weather Agent.

Owner: P3 (Weather & Ocean Data Engineer).
Implements: FR-WX-1 to FR-WX-4.
Reference: LLD v1.0 §2.3.

Thin agent: it asks WeatherDataAdapter (app/data_access) for the merged
provider payload and maps it onto the WeatherResult contract. All provider-
specific knowledge lives in the adapter; all failure handling is driven by
AdapterResult.status (LLD §2.9).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from app.agents._location import as_latlon
from app.data_access.weather_adapter import WeatherDataAdapter
from app.schemas.common import LatLon, TimeWindow
from app.schemas.weather import WeatherResult

logger = logging.getLogger(__name__)


def _unavailable() -> WeatherResult:
    """FR-WX-4 / NFR-REL-1: an explicit 'data unavailable' WeatherResult — no
    fabricated numbers. Matches the sentinel the orchestration graph uses for a
    timed-out / crashed agent, so Risk/Safety treats them identically."""
    return WeatherResult(
        wind_speed_kmh=0.0,
        wave_height_m=0.0,
        active_alerts=[],
        data_timestamp=None,
        status="unavailable",
    )


class WeatherAgent:
    def __init__(self, adapter: WeatherDataAdapter) -> None:
        self._adapter = adapter

    def get_conditions(
        self, location: LatLon | dict[str, Any], window: TimeWindow | None = None
    ) -> WeatherResult:
        """Current wind / wave conditions and any active alerts for a location.

        `location` is a LatLon per LLD §2.3, but a plain {"lat","lon"} dict is
        also accepted while graph._location_for()'s TODO(P1) conversion is
        pending. `window` is accepted (the graph passes it through from the
        Planner) but not yet used — current conditions + active alerts cover
        FR-WX-1/2; forecast-range selection is a later sprint. Never raises:
        any adapter failure becomes status='unavailable' (FR-WX-4)."""
        loc = as_latlon(location)
        if loc is None:
            logger.info("WeatherAgent: no usable location in %r", location)
            return _unavailable()

        result = self._adapter.fetch(
            {"lat": loc.lat, "lon": loc.lon, "window": window}
        )

        if result.status != "ok" or result.data is None:
            logger.info("WeatherAgent: adapter unavailable for (%s, %s)", loc.lat, loc.lon)
            return _unavailable()

        data = result.data
        return WeatherResult(
            wind_speed_kmh=float(data["wind_speed_kmh"]),   # FR-WX-1
            wave_height_m=float(data["wave_height_m"]),      # FR-WX-1
            active_alerts=list(data.get("active_alerts", [])),  # FR-WX-2
            data_timestamp=_timestamp(data, result.fetched_at),  # FR-WX-3
            status="ok",
        )


def _timestamp(data: dict, fallback: datetime) -> datetime:
    """FR-WX-3: surface the observation time. Open-Meteo gives a unix epoch;
    fall back to the adapter's fetch time if it is missing."""
    epoch = data.get("data_time_epoch")
    if epoch is not None:
        try:
            return datetime.fromtimestamp(int(epoch), tz=UTC)
        except (TypeError, ValueError, OSError):
            pass
    return fallback
