"""
Shared pytest fixtures.

Owner: P1 (Backend/Orchestration Lead).
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clear_incois_pfz_cache():
    """INCOISAdapter caches successful PFZ fetches per UTC day at module
    scope (P1 contract-lock decision, 2026-09-01) so the cache survives
    across the per-request INCOISAdapter instances the orchestration graph
    creates (see graph.run_query's TODO(P1) on not caching the compiled
    graph either). That same module-level scope would otherwise leak a
    result from one test into the next test that fetches 'pfz' the same
    day. Clear it before every test so each test's monkeypatched adapter
    behaviour is actually exercised rather than served from a previous
    test's cache entry."""
    from app.data_access import incois_adapter

    with incois_adapter._PFZ_CACHE_LOCK:
        incois_adapter._PFZ_CACHE.clear()
    yield
