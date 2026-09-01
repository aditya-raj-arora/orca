"""
INCOISAdapter — the ONLY module permitted to know INCOIS's specific data
access method (API vs. scraping — this must be confirmed per SRS §6.4 "INCOIS
PFZ data access method (API vs. scraping) confirmed and a sample successfully
retrieved" — do this on Day 1, it blocks OceanAgent).

Owner: P3 (Weather & Ocean Data Engineer).
Reference: LLD v1.0 §2.9.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.config import get_settings
from app.data_access.base import AdapterResult, DataSourceAdapter


class INCOISAdapter(DataSourceAdapter):
    def __init__(self) -> None:
        self._base_url = get_settings().incois_base_url

    def fetch(self, params: dict[str, Any]) -> AdapterResult:
        """
        TODO(P3):
          - params expected: {"lat": float, "lon": float} or a region key,
            depending on what's confirmed feasible against INCOIS (SRS §6.4).
          - Retrieve PFZ centroids and/or SST/chlorophyll for the region.
          - Same error contract as WeatherDataAdapter.fetch(): never raise,
            return status='unavailable' on failure.
        """
        raise NotImplementedError

    def _now(self) -> datetime:
        return datetime.now(UTC)
