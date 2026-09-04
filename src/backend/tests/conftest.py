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


@pytest.fixture(autouse=True)
def _clear_weather_rate_limit_state():
    """Same hazard, same reason (#106): WeatherDataAdapter keeps its result
    cache and its 429 cooldowns at module scope so they survive the
    per-request adapter instances the graph builds. Without this, one test's
    successful fetch would be replayed to the next test querying the same
    ~5 km cell, and one test's simulated 429 would suppress the next test's
    requests entirely."""
    from app.data_access import weather_adapter

    weather_adapter._reset_rate_limit_state()
    yield


@pytest.fixture(autouse=True)
def _clear_geocode_cache():
    """GeocodingAdapter caches successful lookups at module scope for the life
    of the process (#110) — place coordinates don't change. Same leak hazard as
    the two caches above: one test's stubbed lookup would answer the next
    test's."""
    from app.data_access import geocoding_adapter

    geocoding_adapter._reset_cache()
    yield
