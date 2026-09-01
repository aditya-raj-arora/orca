"""
Unit tests for PfzCachingAdapter (get-ahead on #32 / HLD §9 RISK-1 seam).
Owner: P3.
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.data_access.base import AdapterResult
from app.data_access.caching import PfzCachingAdapter

_FT = datetime(2026, 9, 1, tzinfo=UTC)


class _Inner:
    """A DataSourceAdapter stub that returns queued results and records calls."""

    def __init__(self, results: list[AdapterResult]) -> None:
        self._results = list(results)
        self.calls: list[dict] = []

    def fetch(self, params: dict) -> AdapterResult:
        self.calls.append(dict(params))
        return self._results.pop(0)


def _ok(tag: str = "a") -> AdapterResult:
    return AdapterResult({"pfz": [{"lat": 13.0, "lon": 80.0}], "tag": tag}, _FT, "ok")


def _unavail() -> AdapterResult:
    return AdapterResult(None, _FT, "unavailable")


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_second_pfz_fetch_is_served_from_cache() -> None:
    inner = _Inner([_ok("first"), _ok("second")])
    ad = PfzCachingAdapter(inner, ttl_seconds=100.0, clock=_Clock())
    r1 = ad.fetch({"kind": "pfz"})
    r2 = ad.fetch({"kind": "pfz"})
    assert r1 is r2
    assert r1.data["tag"] == "first"
    assert len(inner.calls) == 1  # inner hit once


def test_cache_expires_after_ttl() -> None:
    clock = _Clock()
    inner = _Inner([_ok("first"), _ok("second")])
    ad = PfzCachingAdapter(inner, ttl_seconds=100.0, clock=clock)
    ad.fetch({"kind": "pfz"})
    clock.t = 150.0
    r = ad.fetch({"kind": "pfz"})
    assert r.data["tag"] == "second"
    assert len(inner.calls) == 2


def test_non_pfz_calls_pass_through_every_time() -> None:
    inner = _Inner([_ok(), _ok(), _ok()])
    ad = PfzCachingAdapter(inner, clock=_Clock())
    ad.fetch({"kind": "ocean_params", "lat": 1, "lon": 2})
    ad.fetch({"kind": "ocean_params", "lat": 1, "lon": 2})
    assert len(inner.calls) == 2
    assert all(c["kind"] == "ocean_params" for c in inner.calls)


def test_stale_fallback_served_when_live_fetch_fails() -> None:
    clock = _Clock()
    inner = _Inner([_ok("good"), _unavail()])
    ad = PfzCachingAdapter(inner, ttl_seconds=10.0, clock=clock)
    ad.fetch({"kind": "pfz"})          # caches "good"
    clock.t = 20.0                     # cache now expired
    r = ad.fetch({"kind": "pfz"})      # inner fails
    assert r.status == "stale"         # NFR-REL-1: not presented as live
    assert r.data["tag"] == "good"
    assert len(inner.calls) == 2


def test_failure_propagates_when_nothing_cached() -> None:
    ad = PfzCachingAdapter(_Inner([_unavail()]), clock=_Clock())
    r = ad.fetch({"kind": "pfz"})
    assert r.status == "unavailable"
    assert r.data is None


def test_stale_result_from_inner_is_cached() -> None:
    stale = AdapterResult({"pfz": [{"lat": 1, "lon": 2}], "tag": "s"}, _FT, "stale")
    inner = _Inner([stale, _ok("fresh")])
    ad = PfzCachingAdapter(inner, ttl_seconds=100.0, clock=_Clock())
    assert ad.fetch({"kind": "pfz"}).status == "stale"
    assert ad.fetch({"kind": "pfz"}).data["tag"] == "s"  # served from cache
    assert len(inner.calls) == 1
