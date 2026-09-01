"""
PfzCachingAdapter — the HLD §9 / RISK-1 seam made concrete for the PFZ feed.

INCOISAdapter.fetch({"kind": "pfz"}) pulls the whole ~1.3 MB
PFZ_Automation:pfzlines GeoJSON. The advisory changes at most once a day, so
re-fetching it on every query wastes the NFR-PERF-1 budget. This decorator
wraps any DataSourceAdapter and:

  * serves a cached successful 'pfz' result for up to `ttl_seconds`;
  * on a later live-fetch failure, serves the last good snapshot marked
    status='stale' — never presented as live (NFR-REL-1);
  * passes every non-'pfz' call straight through, unchanged.

Wiring (P1, alongside graph._location_for()'s TODO):
    ocean_agent = OceanAgent(PfzCachingAdapter(INCOISAdapter()))

Owner: P3. Base contract: app/data_access/base.py (unchanged).
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from app.data_access.base import AdapterResult, DataSourceAdapter

logger = logging.getLogger(__name__)

_DEFAULT_TTL_S = 6 * 3600.0  # PFZ advisory cadence is daily; 6h keeps it fresh
                             # while collapsing repeat queries within a session.


class PfzCachingAdapter(DataSourceAdapter):
    def __init__(
        self,
        inner: DataSourceAdapter,
        ttl_seconds: float = _DEFAULT_TTL_S,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._inner = inner
        self._ttl = ttl_seconds
        self._clock = clock
        self._cached: AdapterResult | None = None
        self._cached_at: float = 0.0

    def fetch(self, params: dict[str, Any]) -> AdapterResult:
        if params.get("kind", "pfz") != "pfz":
            return self._inner.fetch(params)

        if self._cached is not None and (self._clock() - self._cached_at) < self._ttl:
            return self._cached

        result = self._inner.fetch(params)
        if result.status in ("ok", "stale") and result.data:
            self._cached = result
            self._cached_at = self._clock()
            return result

        # Live fetch failed. Fall back to the last good snapshot as 'stale'
        # (HLD §9 RISK-1); if we have nothing cached, propagate the failure.
        if self._cached is not None:
            logger.warning("PfzCachingAdapter: live PFZ fetch failed, serving stale cache")
            return AdapterResult(
                data=self._cached.data,
                fetched_at=self._cached.fetched_at,
                status="stale",
            )
        return result
