"""
WeatherDataAdapter — the ONLY module permitted to know the weather provider's
specific API shape (IMD public bulletins / OpenWeather Marine API — confirm
which, per HLD §6 and SRS §6.4 dependency list).

Owner: P3 (Weather & Ocean Data Engineer).
Reference: LLD v1.0 §2.9.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.config import get_settings
from app.data_access.base import AdapterResult, DataSourceAdapter


class WeatherDataAdapter(DataSourceAdapter):
    def __init__(self) -> None:
        settings = get_settings()
        self._api_key = settings.weather_api_key
        self._base_url = settings.weather_api_base_url

    def fetch(self, params: dict[str, Any]) -> AdapterResult:
        """
        TODO(P3):
          - params expected: {"lat": float, "lon": float, "window": TimeWindow}
          - Call the confirmed weather provider's REST endpoint.
          - On any error/timeout/non-2xx, return
            AdapterResult(data=None, fetched_at=now, status='unavailable') —
            do NOT raise up to WeatherAgent (LLD §2.9 contract).
          - On success, return AdapterResult(data=<raw provider payload
            normalised to a plain dict>, fetched_at=now, status='ok').
        """
        raise NotImplementedError

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)
