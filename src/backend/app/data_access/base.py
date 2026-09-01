"""
DataSourceAdapter protocol — the common interface every external-data adapter
must implement.

Reference: LLD v1.0 §2.9. This is the seam identified in HLD §9 for future
fallback/cache insertion (RISK-1: live-only data strategy). Adding a
cached-snapshot fallback later should mean adding a decorator around fetch()
here, not modifying any agent — keep it that way.

Owned jointly: P3 (Weather/Ocean adapters), P4 (GIS boundary adapter). Do not
change this base contract without a `contract-change` issue (CONTRIBUTING.md
§6) since both owners' adapters depend on it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

AdapterStatus = Literal["ok", "unavailable", "stale"]


@dataclass
class AdapterResult:
    data: dict[str, Any] | None
    fetched_at: datetime
    status: AdapterStatus


class DataSourceAdapter(Protocol):
    def fetch(self, params: dict[str, Any]) -> AdapterResult:
        """Every implementation MUST return AdapterResult rather than raising
        on a data-unavailable condition (LLD §2.9), so agents can implement
        FR-WX-4 / FR-OCEAN-4 uniformly by checking `.status`."""
        ...
