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


@pytest.fixture(autouse=True)
def _clear_http_cooldowns():
    """The 429 cooldown registry moved to data_access/http_client.py in #116
    and is now shared by WeatherDataAdapter and GeocodingAdapter. Clearing it
    via weather_adapter._reset_rate_limit_state() above would work by
    accident; clear it explicitly so a geocoding-only test does not inherit a
    weather test's cooldown (and vice versa)."""
    from app.data_access import http_client

    http_client.reset_cooldowns()
    yield


@pytest.fixture(autouse=True)
def _clear_synthesis_sentence_cache():
    """Fifth instance of the same hazard (#143): SynthesisAgent caches
    successful compositions at module scope, keyed on the prompt payload, so
    the cache survives the per-request agent instances the graph builds.

    Without this, a test whose fixture produces the same agent outputs as an
    earlier one is served that earlier composition and never calls its own
    stubbed LLM at all — which is exactly how four call-count assertions in
    tests/unit/test_synthesis_agent.py started failing the moment the cache
    landed."""
    from app.orchestration import synthesis_agent

    synthesis_agent.reset_sentence_cache()
    yield
