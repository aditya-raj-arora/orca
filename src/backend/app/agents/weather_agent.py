"""
Weather Agent.

Owner: P3 (Weather & Ocean Data Engineer).
Implements: FR-WX-1 to FR-WX-4.
Reference: LLD v1.0 §2.3.
"""
from __future__ import annotations

from app.data_access.weather_adapter import WeatherDataAdapter
from app.schemas.common import LatLon, TimeWindow
from app.schemas.weather import WeatherResult


class WeatherAgent:
    def __init__(self, adapter: WeatherDataAdapter) -> None:
        self._adapter = adapter

    def get_conditions(self, location: LatLon, window: TimeWindow) -> WeatherResult:
        """Delegates the HTTP call to WeatherDataAdapter (app/data_access).

        TODO(P3):
          - Call self._adapter.fetch(...) with the right params for this
            provider (HLD §6: IMD public bulletins or OpenWeather Marine API —
            confirm which per SRS §6.4).
          - On AdapterResult.status != 'ok', return WeatherResult(status=
            'unavailable', ...) — do NOT raise, and do NOT fabricate a value
            (FR-WX-4). This is what lets the Risk/Safety Agent correctly return
            INSUFFICIENT_DATA (NFR-REL-2) instead of crashing or guessing SAFE.
          - Timestamp every result (FR-WX-3) — surfaced to the end user.
          - Populate active_alerts (cyclone, lightning, high-wave — FR-WX-2).
        """
        raise NotImplementedError
