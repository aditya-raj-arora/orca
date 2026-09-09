"""
Shared outbound-HTTP policy for the data_access adapters: bounded retry,
Retry-After handling, and a per-source 429 cooldown.

Owner: P3 (extracted from weather_adapter.py in #116; the behaviour and the
constants are unchanged apart from the 429 rule below).

WHY THIS IS ITS OWN MODULE (#116). Open-Meteo meters its keyless tier per
CLIENT IP, not per API key — it has no keys on that tier. On Render's free plan
our egress IP is shared with every other service on the node, so the quota we
are spending is a quota we do not control: a neighbouring service can exhaust
it, and it stays exhausted for the rest of the hour/day window regardless of
what we do. That is the "always hitting the rate limit" symptom.

Two consequences shape this module:

  1. Every Open-Meteo call we make has to earn its place, INCLUDING the ones
     outside WeatherDataAdapter. GeocodingAdapter (#110) is a third Open-Meteo
     endpoint that was calling httpx.get directly with no cooldown and no
     retry, so a weather 429 taught us nothing about the geocoding call we
     made 200 ms later. Both adapters now share the cooldown registry here.

  2. A 429 is NEVER retried. #106 retried it up to _MAX_ATTEMPTS with a
     sub-second backoff, which contradicted its own docstring: an IP-level
     minute/hour/day bucket does not clear in 0.75 s, so the two extra attempts
     could only ever add load to an API that had just refused us — and on a
     shared IP that load is what keeps the bucket empty. On 429 we now record
     the cooldown and fail immediately. Retries still apply to 5xx and
     transport errors, which are genuinely transient and are not quota'd.

The real cure for a shared egress IP is not a client-side policy at all: set
OPEN_METEO_API_KEY (see core/config.py) so the quota is billed to the account
instead of to Render's IP. This module keeps us honest and quiet until then.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

HTTP_TIMEOUT_S = 4.0          # per attempt; the weather sources run in parallel,
                              # so well inside the graph's per-node budget.
CONNECT_TIMEOUT_S = 2.0       # slice of an attempt's budget reserved for TCP+TLS
                              # setup — see _attempt_timeout().
SOURCE_BUDGET_S = 5.0         # total wall clock for one source INCLUDING its
                              # retries. Every retry is bounded by this
                              # deadline, so hardening an adapter against 429
                              # can never push a node past its graph budget
                              # (orchestration/graph.py) — a source that runs
                              # out of budget simply reports failure early.
MAX_ATTEMPTS = 3
_MIN_ATTEMPT_S = 0.1          # floor for a last attempt squeezed against the
                              # deadline: a 0s read timeout fails on the spot
                              # and wastes the attempt.
BACKOFF_BASE_S = 0.25         # 0.25s, 0.5s — deliberately short: the budget
MAX_BACKOFF_S = 1.0           # above, not the backoff curve, is the real bound.
RETRYABLE_STATUS = frozenset({500, 502, 503, 504})  # deliberately NOT 429 —
                                                    # see the module docstring.

# --- 429 cooldown ------------------------------------------------------- #
# A 429 from an IP-level quota does not clear in a backoff window, and calling
# again adds load to an API that is already refusing us. After a 429 we stop
# calling that source until Retry-After (or DEFAULT_COOLDOWN_S when the header
# is absent) has passed. Capped so a hostile/garbled header cannot park a
# source for the rest of the demo.
DEFAULT_COOLDOWN_S = 30.0
MAX_COOLDOWN_S = 300.0
_COOLDOWN_LOCK = threading.Lock()
_COOLDOWN: dict[str, float] = {}  # source label -> time.monotonic() deadline


class RateLimitedError(Exception):
    """Raised instead of issuing a request to a source that is inside its 429
    cooldown. Callers catch it like any other source failure — the point is to
    fail without adding load, not to fail differently."""


def cooldown_remaining_s(source: str) -> float:
    with _COOLDOWN_LOCK:
        until = _COOLDOWN.get(source)
    return 0.0 if until is None else max(0.0, until - time.monotonic())


def start_cooldown(source: str, retry_after_s: float | None) -> None:
    delay = DEFAULT_COOLDOWN_S if retry_after_s is None else retry_after_s
    delay = min(max(delay, 0.0), MAX_COOLDOWN_S)
    with _COOLDOWN_LOCK:
        _COOLDOWN[source] = time.monotonic() + delay
    logger.warning(
        "%s rate-limited (429) — pausing calls to it for %.0fs", source, delay
    )


def reset_cooldowns() -> None:
    """Test hook (see tests/conftest.py); not used in production code."""
    with _COOLDOWN_LOCK:
        _COOLDOWN.clear()


def retry_after_s(response: httpx.Response) -> float | None:
    """RFC 9110 Retry-After, delta-seconds form only. The HTTP-date form is
    rare on rate limiters and is not worth a clock-skew bug here — treating it
    as absent just falls back to DEFAULT_COOLDOWN_S."""
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw.strip()))
    except ValueError:
        logger.debug("retry_after_s: non-numeric Retry-After header %r", raw)
        return None


def _attempt_timeout(remaining: float) -> httpx.Timeout:
    """Per-attempt timeouts that actually add up to the budget (#131).

    This used to pass `timeout=min(HTTP_TIMEOUT_S, remaining)` as a bare float,
    which httpx applies to connect, read, write and pool SEPARATELY — so one
    attempt could run for several times the budget it was handed, and
    SOURCE_BUDGET_S was a hint rather than the ceiling its module docstring
    claims. That is how a 429-plus-fallback weather fetch overran the graph's
    6s weather node by ~100ms and had its recovered data thrown away.

    Splitting the attempt's budget instead of repeating it bounds connect+read
    — the only two phases a GET can realistically spend time in. write and pool
    get the (smaller) connect slice: these fetchers send no request body, and
    httpx.get builds a fresh pool per call, so neither can meaningfully fire.
    """
    budget = max(min(HTTP_TIMEOUT_S, remaining), _MIN_ATTEMPT_S)
    connect = min(CONNECT_TIMEOUT_S, budget / 2)
    return httpx.Timeout(connect=connect, read=budget - connect, write=connect, pool=connect)


def proxy_for(source: str) -> str | None:
    """The outbound proxy to use for `source`, or None to go out directly.

    Open-Meteo meters its keyless tier per CLIENT IP and Render's free plan
    shares one egress IP across the node, so the quota is spent by traffic we
    neither generate nor can see — the 429s this module exists to survive. A
    static egress IP (#151) makes the quota ours again.

    Applied per source rather than globally on purpose: only Open-Meteo is
    metered by IP. GDACS is a 1.5 MB feed and WeatherAPI is metered per key,
    so routing either through a bandwidth-metered proxy spends the plan and
    buys nothing.

    The returned value carries credentials. Callers must not log it.
    """
    settings = get_settings()
    proxy = (settings.outbound_proxy_url or "").strip()
    if not proxy:
        return None
    prefixes = [p.strip() for p in settings.outbound_proxy_sources.split(",") if p.strip()]
    return proxy if any(source.startswith(p) for p in prefixes) else None


def get(source: str, url: str, **kwargs: Any) -> httpx.Response:
    """httpx.get hardened against transient upstream refusal.

    Retries 5xx and transport errors up to MAX_ATTEMPTS, with every attempt AND
    every sleep bounded by a SOURCE_BUDGET_S deadline. A 429 is not retried:
    it starts the source's cooldown and raises on the spot. Raises on final
    failure (the caller turns that into its own degrade path) — a non-retryable
    4xx still raises on the first attempt exactly as raise_for_status() did.
    """
    cooling = cooldown_remaining_s(source)
    if cooling > 0:
        raise RateLimitedError(f"{source}: in 429 cooldown for another {cooling:.0f}s")

    # None unless this source is configured to go via a static egress IP
    # (#151). Never logged — it carries credentials.
    proxy = proxy_for(source)
    deadline = time.monotonic() + SOURCE_BUDGET_S
    last_error: Exception | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            response = httpx.get(
                url, timeout=_attempt_timeout(remaining), proxy=proxy, **kwargs
            )
        except httpx.TransportError as exc:  # connect/read/write/pool errors
            logger.debug(
                "%s: attempt %d/%d transport error: %s", source, attempt, MAX_ATTEMPTS, exc
            )
            last_error = exc
        else:
            if response.status_code == 429:
                # Quota, not congestion. Back off at the source level and stop
                # — another attempt now can only make the bucket worse.
                start_cooldown(source, retry_after_s(response))
                raise httpx.HTTPStatusError(
                    f"{source}: 429 from {url}",
                    request=response.request,
                    response=response,
                )
            if response.status_code not in RETRYABLE_STATUS:
                response.raise_for_status()  # non-retryable 4xx -> raise as before
                return response
            last_error = httpx.HTTPStatusError(
                f"{source}: retryable {response.status_code} from {url}",
                request=response.request,
                response=response,
            )

        if attempt == MAX_ATTEMPTS:
            break
        delay = min(MAX_BACKOFF_S, BACKOFF_BASE_S * (2 ** (attempt - 1)))
        if delay >= deadline - time.monotonic():
            break  # no budget left for another attempt; give up now, don't oversleep
        time.sleep(delay)

    assert last_error is not None  # loop only exits early after setting it
    raise last_error
