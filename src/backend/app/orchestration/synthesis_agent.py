"""
Synthesis Agent — composes all invoked agents' outputs into one coherent,
cited, natural-language response.

Owner: P2 (LLM/Synthesis Engineer).
Implements: FR-SYN-1 to FR-SYN-3.
Reference: LLD v1.0 §2.7.

SAFETY-CRITICAL RULE (FR-SYN-2): every sentence in the output must map to a
citation. Do not ship this without the citation-coverage check described below
— an uncited claim in a safety-relevant response is the single worst failure
mode this system can have.

CITATION-COVERAGE APPROACH (documented Issue #4; implemented Issue #9):
  1. compose() asks the LLM for sentence-level JSON — {"text", "source"}
     pairs — instead of free text, so sentence-to-citation mapping doesn't
     need fragile after-the-fact splitting.
  2. Every `source` must be a key in the *available* (non-None) subset of
     `results`, or "none" for a connective sentence with no factual claim.
  3. `_citation_coverage_ok()` checks this with set membership — no
     second LLM call.
  4. On failure, compose() regenerates once, then degrades to a safe
     fallback rather than shipping an unverified sentence.
  5. Citation timestamps come from each result's own `data_timestamp` —
     never fabricated (NFR-REL-1, extended to citations).

RESULTS CAN CONTAIN None (confirmed against app/orchestration/graph.py,
2026-09-02): ocean/geofencing/risk_safety nodes pass `unavailable=None` to
_call_bounded when location isn't resolved or the agent errors, so
`results["ocean"]` etc. may literally be None, distinct from an
agent-returned "status=unavailable" object (e.g. WeatherResult always has a
real object — see _unavailable_weather_result()). compose() must exclude
None entries from the prompt/citations entirely, not describe them as data.
`results["ocean_params"]` (SST/chlorophyll, FR-OCEAN-2, wired 2026-09-02)
follows the WeatherResult convention instead — always a real OceanParams
object, unavailability represented by None fields, not a None entry.

MODEL NOTE: uses gemini-3.5-flash-lite (#168), not gemini-3.6-flash (the
original choice) or gemini-2.5-flash (retired for new API keys — see Issue
#<N>, also affects planner_agent.py). Measured against the real API with the
real prompt, 5 runs each, scored against this file's OWN safety gates rather
than approximated:

    gemini-3.6-flash + LOW (as shipped)   median 24.2s   max 27.5s   5/5 passed
    gemini-3.5-flash-lite                 median  1.7s   max  1.7s   5/5 passed
    gemini-3.5-flash-lite + LOW           median  1.5s   max  1.7s   5/5 passed
    gemini-3.5-flash                      median  8.0s   max  9.9s   5/5 passed
    gemini-3.5-flash + LOW                429 RESOURCE_EXHAUSTED

~15-16x faster, zero variance across 5 runs, and the SAME model
PlannerAgent already uses (planner_agent.py picked it "for its higher
free-tier RPM/RPD" — this run independently confirmed that: the flash/flash
tier ran out of quota partway through the five-candidate experiment while
flash-lite was untouched). Synthesis had been on the tighter tier the whole
time. The 24.2s baseline here is itself informative, not just a large gap
from #137's earlier 3.4s measurement — most likely the same quota
contention, since it ran first in the experiment — which argues for the
swap rather than against measuring it: the old model degrades badly under
exactly the load a real demo produces, and flash-lite did not move.

_THINKING_LEVEL is still applied (harmless no-op here — flash-lite reports
no thought tokens either with or without it, matching planner_agent.py's
#141 finding for the same model) rather than branched per model, so a
future swap back to a thinking-capable model keeps the cap by default
instead of it being silently missing.

Not measured: prose fluency. The safety gates check structure and phrasing,
not quality — but #139/#140 mean a fluency regression ships a working,
cited answer rather than a broken one, which is a real backstop rather than
a reason to skip checking real output before calling this done.

No `temperature` param passed — Gemini 3.x migration notes say sampling
params are unsupported on 3.x models. (flash-lite is on 3.5, kept for
consistency with the rest of this file's config; harmless if unsupported.)
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from typing import Any

from app.core.config import get_settings
from app.schemas.common import Citation
from app.schemas.synthesis import ComposedResponse, ExecutionPlan, MapPayload

logger = logging.getLogger(__name__)

_GEMINI_MODEL = "gemini-3.5-flash-lite"

# --- compose()'s own budget (#133) --------------------------------------- #
# Nothing used to bound a single generate_content call: the client was built
# without a timeout, so when one stalled it ran until graph.py's node timeout
# killed compose() outright. That kill is the worst available outcome — it
# discards _degraded_response() below, which still carries the Risk verdict, in
# favour of the graph's "couldn't put together an answer" sentinel that carries
# nothing. On the deployed backend that threw away a real CAUTION verdict.
#
# So compose() spends a budget of its own and always returns something. Must
# stay under graph.SYNTHESIS_TIMEOUT_SECONDS, which is now only the backstop for
# compose() itself hanging; tests/test_graph.py pins the two together.
#
# Sized around _MIN_API_DEADLINE_S below (#135): one call may legitimately use
# the full 10s the API insists on, and this has to leave room for that plus the
# reserve, or compose() would be killed on exactly the call it was given the
# budget to make.
_DEFAULT_BUDGET_S = 11.0

# Kept back from the last call so compose() can build and return its degraded
# response before the graph's backstop fires. The work is microseconds; this is
# for thread scheduling on a box sharing one core with the rest of the graph.
_BUDGET_RESERVE_S = 0.5

# A Gemini round trip needs more than this to be worth starting. Below it,
# spending the remaining budget on a call that cannot finish only delays the
# degraded response we would return anyway.
_MIN_CALL_BUDGET_S = 2.0

# Gemini REJECTS a shorter deadline than this outright (#135):
#   400 INVALID_ARGUMENT "Manually set deadline 5s is too short.
#                         Minimum allowed deadline is 10s."
# #134 shipped 8.5s here and turned every call into an instant 400 in
# production. The deadline is a ceiling, not a reservation — a call that
# answers in 2s still answers in 2s — so we send the floor when our own budget
# is smaller, and let compose()'s budget decide whether starting a call is
# worth it. graph.SYNTHESIS_TIMEOUT_SECONDS must stay above this, or a call
# using its full deadline gets killed before compose() can degrade gracefully.
_MIN_API_DEADLINE_S = 10.0

# HISTORICAL (#137) — measured against gemini-3.6-flash, the model this file
# used at the time. Kept for the reasoning trail: it is why _THINKING_LEVEL
# exists at all and why it defaults to LOW rather than being unset. #168
# swapped the model to gemini-3.5-flash-lite, which reports NO thought tokens
# with or without this setting (same finding planner_agent.py already made
# for the same model, #141) — so none of the numbers below describe current
# behaviour, only the problem LOW was built to solve.
#
# Gemini 3.x thinks before it answers, and on this call it thought far more
# than it wrote — 1185 thought tokens against 260 output tokens. Measured on
# the real API with this exact prompt, 3 runs each (#137):
#
#   as shipped (no thinking_config)   min 5.7s   median 9.8s   max 11.2s
#   thinking_budget=0                 400 INVALID_ARGUMENT — rejected outright
#   thinking_level=MINIMAL            min 3.3s   median 5.0s   max 30.5s
#   thinking_level=LOW                min 2.3s   median 3.4s   max 14.9s
#
# The default median lands ON the 10s deadline the API enforces, so the
# deployed query 504'd about as often as not — a coin flip, not an anomaly.
# LOW moves the median to 3.4s, comfortably inside it.
#
# Note thinking_budget=0 is NOT available here despite the SDK documenting
# "0 is DISABLED" — this model refuses it with a 400, so the level enum is the
# only lever. MINIMAL is not obviously better than LOW and had the worse tail
# in the sample.
#
# This is a median fix, not a guarantee: LOW still spiked to 14.9s in 3 runs,
# and the deadline cannot go below _MIN_API_DEADLINE_S to compensate. The
# degraded path below stays load-bearing — it just stops being the routine
# outcome. Composition quality is protected by the citation/phrasing checks
# either way; if less thinking starts tripping them, the regeneration warning
# in compose() says so.
_THINKING_LEVEL = "LOW"

# One client, and therefore one connection pool, for every SynthesisAgent
# instance (#149) — see _build_llm_client().
_SHARED_LLM_CLIENT: Any = None
_SHARED_LLM_CLIENT_LOCK = threading.Lock()


def reset_shared_llm_client() -> None:
    """Test hook (see tests/conftest.py). Not used in production code."""
    global _SHARED_LLM_CLIENT
    with _SHARED_LLM_CLIENT_LOCK:
        _SHARED_LLM_CLIENT = None

_SYNTHESIS_SYSTEM_PROMPT = """You are the response-composition step of a marine \
safety assistant. You will be given the outputs of one or more specialist \
agents (weather, ocean, ocean_params, geofencing, risk_safety) as JSON, plus \
the target response language.

Rules (do not break these):
1. Use ONLY the facts present in the agent outputs given to you. Never add \
outside knowledge, never guess a number, never invent a place name.
2. If an agent's status is "unavailable" or a verdict is "INSUFFICIENT_DATA", \
say so plainly in the response — never phrase missing/insufficient data in a \
way that could read as "safe" or "fine".
2a. The weather agent output may include "alerts_source_available": false. \
This means BOTH severe-weather alert sources were unreachable, so an empty \
"active_alerts" list does NOT mean "no alerts" — it means alert status is \
unknown. When you see alerts_source_available is false, say that alert data \
is currently unavailable / could not be checked. Never say or imply "no \
active alerts" or "conditions are clear" in that case, even if wind/wave/other \
weather fields are present and look fine.
2b. If the risk_safety verdict is "UNSAFE", state that plainly and keep it \
plain. Do not soften it, do not bury it after reassuring detail, and do not \
offset it with a "but" clause about favourable conditions — a calm sea does \
not make an UNSAFE verdict less UNSAFE. Favourable weather or ocean facts may \
still be reported, but never as a reason to discount the verdict.
3. Write the response text in the requested language.
4. Break your response into individual sentences. EVERY sentence that states \
a fact from an agent's output must be tagged with that agent's name as its \
source. A purely connective/transitional sentence with no factual claim may \
use source "none".

Respond with ONLY a JSON object, no other text, in this exact shape:
{"sentences": [{"text": str, "source": str}, ...]}

Valid "source" values are exactly the agent names provided to you, or "none".
Do not invent a source value that wasn't given to you.
"""


def _serialize_result(result: object) -> dict:
    """Best-effort JSON-safe conversion of an agent result (WeatherResult,
    PFZResult, GeofenceResult, RiskVerdict — dataclasses per the LLD) for
    the prompt. Also accepts plain dicts (fixture-friendly for testing
    before every real agent exists). Callers must exclude None entries
    before calling this — see compose()."""
    if is_dataclass(result) and not isinstance(result, type):
        data = asdict(result)
    elif isinstance(result, dict):
        data = dict(result)
    else:
        data = {"value": str(result)}

    def _default(o):
        if isinstance(o, datetime):
            return o.isoformat()
        return str(o)

    return _round_floats(json.loads(json.dumps(data, default=_default)))


def _round_floats(value: object, places: int = 2) -> object:
    """Round every float in the prompt payload (#118).

    The LLM verbalises these numbers directly, and full float precision leaks
    into the answer — a real response read "sitting 277.6379475729435 km from
    the IMBL". The Risk agent already formats its own strings with :.1f; this
    does the same job for the values Synthesis hands the model raw, rather than
    hoping the model rounds them.

    Prompt-payload only: nothing downstream computes on these, so this cannot
    affect a verdict or a distance check — it only changes how a number reads
    in prose. bool is excluded because it is an int subclass, not a quantity.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return round(value, places)
    if isinstance(value, dict):
        return {k: _round_floats(v, places) for k, v in value.items()}
    if isinstance(value, list):
        return [_round_floats(v, places) for v in value]
    return value


# --- successful-composition cache (#143) --------------------------------- #
# The Gemini call misses often enough on the deployed instance that an
# identical repeat question re-rolls the same dice. Caching a composition that
# SUCCEEDED lets the repeat return the good answer instantly.
#
# Keyed on the whole prompt payload, which carries every agent's
# data_timestamp — so a hit means the underlying data is genuinely unchanged,
# not merely a similar-looking question. Two rules matter more than the
# caching itself, and both are enforced at the call site in compose():
#   1. Only sentences that passed _response_is_safe() are ever stored.
#   2. The degraded/deterministic fallback is NEVER stored. Caching it would
#      freeze a bad outcome for the whole TTL and suppress the retry that
#      might have succeeded — and it is pure Python to recompute anyway.
# Sentences are cached rather than the ComposedResponse so the trace stays
# per-query.
_SENTENCE_CACHE: dict[str, tuple[float, list[dict]]] = {}
_SENTENCE_CACHE_LOCK = threading.Lock()
_SENTENCE_CACHE_TTL_S = 600.0
_SENTENCE_CACHE_MAX_ENTRIES = 128


def _prompt_cache_key(prompt_payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(prompt_payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _sentences_cache_get(key: str) -> list[dict] | None:
    now = time.monotonic()
    with _SENTENCE_CACHE_LOCK:
        entry = _SENTENCE_CACHE.get(key)
        if entry is None:
            return None
        stored_at, sentences = entry
        if now - stored_at > _SENTENCE_CACHE_TTL_S:
            del _SENTENCE_CACHE[key]
            return None
    # Copied out: callers build a response from these and must not be able to
    # mutate what the next query will read.
    return [dict(s) for s in sentences]


def _sentences_cache_put(key: str, sentences: list[dict]) -> None:
    now = time.monotonic()
    with _SENTENCE_CACHE_LOCK:
        _SENTENCE_CACHE[key] = (now, [dict(s) for s in sentences])
        if len(_SENTENCE_CACHE) > _SENTENCE_CACHE_MAX_ENTRIES:
            for stale in [
                k for k, (t, _) in _SENTENCE_CACHE.items() if now - t > _SENTENCE_CACHE_TTL_S
            ]:
                del _SENTENCE_CACHE[stale]
        if len(_SENTENCE_CACHE) > _SENTENCE_CACHE_MAX_ENTRIES:  # still full: drop oldest
            oldest = min(_SENTENCE_CACHE, key=lambda k: _SENTENCE_CACHE[k][0])
            del _SENTENCE_CACHE[oldest]


def reset_sentence_cache() -> None:
    """Test hook — mirrors http_client.reset_cooldowns()."""
    with _SENTENCE_CACHE_LOCK:
        _SENTENCE_CACHE.clear()


def _field(result: object, name: str, default: Any = None) -> Any:
    """Read one field off an agent result that may be a dataclass (what the
    graph passes) or a plain dict (what fixtures pass) — the same dual shape
    _verdict_phrased_safely() already handles."""
    if isinstance(result, dict):
        return result.get(name, default)
    return getattr(result, name, default)


def _deterministic_sentences(available_results: dict[str, object]) -> list[dict]:
    """Agent outputs -> {"text", "source"} pairs, the same shape the LLM is
    asked for, so everything downstream (safety checks, citations) is shared
    (#139).

    The wording is not arbitrary: it has to pass _response_is_safe() by
    construction, which constrains two things in particular.
      - When alerts_source_available is False, a weather sentence must flag
        alert status as unknown and must avoid reading as an all-clear
        (_alerts_unavailable_phrased_safely).
      - An UNSAFE / INSUFFICIENT_DATA verdict must actually be stated, and no
        risk_safety sentence may read as reassurance (_verdict_phrased_safely).
    Risk goes last so the verdict reads as the conclusion of the facts above
    it, which is also how the LLM is prompted to order it.
    """
    sentences: list[dict] = []

    weather = available_results.get("weather")
    if weather is not None:
        sentences.extend(_weather_sentences(weather))

    geofence = available_results.get("geofencing")
    if geofence is not None:
        sentences.extend(_geofence_sentences(geofence))

    ocean = available_results.get("ocean")
    if ocean is not None:
        sentences.extend(_ocean_sentences(ocean))

    ocean_params = available_results.get("ocean_params")
    if ocean_params is not None:
        sentences.extend(_ocean_param_sentences(ocean_params))

    risk = available_results.get("risk_safety")
    if risk is not None:
        sentences.extend(_risk_sentences(risk))

    return sentences


def _weather_sentences(weather: object) -> list[dict]:
    def _s(text: str) -> dict:
        return {"text": text, "source": "weather"}

    if _field(weather, "status") == "unavailable":
        return [_s("Weather data is unavailable for this location.")]

    out: list[dict] = []
    wind = _field(weather, "wind_speed_kmh")
    wave = _field(weather, "wave_height_m")
    if wind is not None and wave is not None:
        out.append(_s(f"Wind is {wind:.0f} km/h with a wave height of {wave:.1f} m."))
    elif wind is not None:
        out.append(_s(f"Wind is {wind:.0f} km/h."))
    elif wave is not None:
        out.append(_s(f"Wave height is {wave:.1f} m."))

    visibility_m = _field(weather, "visibility_m")
    if visibility_m is not None:
        out.append(_s(f"Visibility is about {visibility_m / 1000:.1f} km."))

    alerts = _field(weather, "active_alerts") or []
    if not _field(weather, "alerts_source_available", True):
        # NFR-REL-2: an empty list here means "unknown", never "clear". The
        # wording must say so without any of the all-clear phrasing
        # _alerts_unavailable_phrased_safely() rejects.
        out.append(
            _s(
                "Severe-weather alert sources could not be reached, so whether any "
                "alerts are active is unknown."
            )
        )
    elif alerts:
        out.append(_s("Active weather alerts: " + "; ".join(alerts) + "."))
    else:
        out.append(_s("No active weather alerts were reported."))
    return out


def _geofence_sentences(geofence: object) -> list[dict]:
    def _s(text: str) -> dict:
        return {"text": text, "source": "geofencing"}

    out: list[dict] = []
    distance_km = _field(geofence, "imbl_distance_km")
    if _field(geofence, "within_mpa"):
        name = _field(geofence, "mpa_name") or "an unnamed area"
        out.append(_s(f"The location is inside the Marine Protected Area {name}."))
    if _field(geofence, "within_imbl_buffer"):
        out.append(
            _s(
                "The location is inside the international maritime boundary buffer"
                + (f", {distance_km:.1f} km from the line." if distance_km is not None else ".")
            )
        )
    if not out:
        out.append(
            _s(
                "The location is outside any Marine Protected Area"
                + (
                    f" and {distance_km:.1f} km from the international maritime boundary."
                    if distance_km is not None
                    else "."
                )
            )
        )
    return out


def _ocean_sentences(ocean: object) -> list[dict]:
    def _s(text: str) -> dict:
        return {"text": text, "source": "ocean"}

    distance_km = _field(ocean, "distance_km")
    if distance_km is None:
        return []
    text = f"The nearest potential fishing zone advisory is about {distance_km:.0f} km away."
    if _field(ocean, "is_stale"):
        # FR-OCEAN-4: a stale advisory is reported, never silently trusted.
        text += " That advisory is stale, so treat it as indicative only."
    return [_s(text)]


def _ocean_param_sentences(params: object) -> list[dict]:
    def _s(text: str) -> dict:
        return {"text": text, "source": "ocean_params"}

    facts: list[str] = []
    sst = _field(params, "sea_surface_temp_c")
    if sst is not None:
        facts.append(f"sea surface temperature {sst:.1f} °C")
    chlorophyll = _field(params, "chlorophyll_mg_m3")
    if chlorophyll is not None:
        facts.append(f"chlorophyll {chlorophyll:.2f} mg/m³")
    # FR-OCEAN-2: None means the region publishes no value — say nothing rather
    # than reporting an absence as a reading.
    return [_s("Ocean conditions: " + " and ".join(facts) + ".")] if facts else []


def _risk_sentences(risk: object) -> list[dict]:
    def _s(text: str) -> dict:
        return {"text": text, "source": "risk_safety"}

    verdict = _field(risk, "verdict")
    if not verdict:
        return []
    out = [_s(f"Safety assessment: {verdict}.")]
    rationale = (_field(risk, "rationale") or "").strip()
    if rationale:
        # Risk composes this itself, already human-readable and already
        # carrying its source timestamps (FR-RISK-2) — it needs no rewording.
        out.append(_s(rationale))
    return out


def _contains_phrase(text: str, phrase: str) -> bool:
    """Substring match that won't fire mid-word. Needed because the phrase
    lists below overlap each other as raw substrings — "safe to" sits inside
    "unsafe to", so a plain `in` would reject the single most likely correct
    UNSAFE response ("it is unsafe to fish here") as reassurance. Both `text`
    and `phrase` are expected lowercase."""
    return re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", text) is not None


def _extract_data_timestamp(result: object) -> datetime:
    """Pulls a citation timestamp from an agent result. Falls back to now()
    (UTC) only if the result genuinely has none — real LLD dataclasses all
    carry data_timestamp, so this fallback firing is itself a signal
    something upstream is wrong."""
    ts = getattr(result, "data_timestamp", None)
    if ts is None and isinstance(result, dict):
        ts = result.get("data_timestamp")
    if isinstance(ts, datetime):
        return ts
    logger.warning(
        "Synthesis: agent result has no data_timestamp — falling back to "
        "now(). Should not happen with real agent outputs."
    )
    return datetime.now(UTC)


class SynthesisAgent:
    def __init__(
        self, llm_client: object | None = None, budget_s: float | None = None
    ) -> None:
        # Same lazy-construction pattern as PlannerAgent — unit tests never
        # need a real API key.
        self._llm_client = llm_client
        # Overridable per-instance so tests can drive the out-of-budget paths
        # without sleeping through the real thing (same pattern as
        # RiskSafetyAgent's thresholds).
        self._budget_s = _DEFAULT_BUDGET_S if budget_s is None else budget_s

    def compose(
        self,
        plan: ExecutionPlan,
        results: dict[str, object],
        language: str,
    ) -> ComposedResponse:
        """FR-SYN-1/2/3. Called synchronously by graph.py's _call_bounded via
        asyncio.to_thread — do not make this async (see module docstring)."""
        # graph.py can hand us None-valued entries (agent not requested /
        # location unresolved / errored with no fallback object) — those are
        # not data and must never reach the prompt or citations.
        available_results = {k: v for k, v in results.items() if v is not None}

        if not available_results:
            logger.info("Synthesis: no available agent results — returning degraded response.")
            return self._degraded_response(available_results, plan)

        prompt_payload = {
            "language": language,
            "agents": {name: _serialize_result(r) for name, r in available_results.items()},
        }

        # #143: an identical payload we have already composed successfully
        # needs no model call at all — and skipping it turns what might have
        # been a fallback into the good answer.
        cache_key = _prompt_cache_key(prompt_payload)
        cached = _sentences_cache_get(cache_key)
        if cached is not None:
            logger.info("Synthesis: reusing a cached composition (#143).")
            return self._response_from(cached, available_results, plan)

        # #133: every path from here returns a real response. compose() spends
        # its own budget rather than letting the graph kill it mid-call, because
        # _degraded_response() below still carries the verdict and the graph's
        # sentinel does not.
        deadline = time.monotonic() + self._budget_s

        sentences = self._generate_within(prompt_payload, deadline, attempt="first")
        if sentences is None:
            return self._compose_without_llm(available_results, plan)

        if not self._response_is_safe(sentences, available_results):
            logger.warning(
                "Synthesis: safety check failed on first attempt — "
                "regenerating once (FR-SYN-2 / NFR-REL-1)."
            )
            regenerated = self._generate_within(prompt_payload, deadline, attempt="regeneration")
            if regenerated is None:
                # Out of budget or the call failed. The first attempt is still
                # unsafe and must not ship — compose the answer ourselves,
                # exactly as a second failed check would (FR-SYN-2).
                return self._compose_without_llm(available_results, plan)
            sentences = regenerated

        if not self._response_is_safe(sentences, available_results):
            logger.error(
                "Synthesis: safety check failed twice — refusing to ship an "
                "uncited or falsely-reassuring claim. Composing without the LLM."
            )
            return self._compose_without_llm(available_results, plan)

        # Only reached once the sentences have passed every safety check, which
        # is the only thing that may be cached (#143). Both fallback paths
        # return above without storing anything.
        _sentences_cache_put(cache_key, sentences)
        return self._response_from(sentences, available_results, plan)

    def _response_from(
        self,
        sentences: list[dict],
        available_results: dict[str, object],
        plan: ExecutionPlan,
    ) -> ComposedResponse:
        """Sentences -> the shipped response. Shared by a fresh composition and
        a cached one so a cache hit can't drift from a live answer; the trace is
        rebuilt from the CURRENT plan rather than whatever was cached."""
        return ComposedResponse(
            text=" ".join(s["text"] for s in sentences),
            citations=self._build_citations(sentences, available_results),
            # TODO(P2, Issue #14): populate from geofence/ocean results once
            # real agents land — coordinate exact marker/zone shape with P6
            # (owns Leaflet rendering, see MapPayload TODO).
            map_payload=MapPayload(),
            trace=list(plan.trace),
        )

    def _generate_within(
        self, prompt_payload: dict, deadline: float, attempt: str
    ) -> list[dict] | None:
        """One bounded generation, or None if it can't or didn't produce one.

        None is not an error path the caller has to distinguish — every reason
        we return it (no budget left, the call failed, the call timed out) leads
        to the same place: a degraded response that still carries the verdict.
        """
        budget_s = deadline - time.monotonic() - _BUDGET_RESERVE_S
        if budget_s < _MIN_CALL_BUDGET_S:
            logger.warning(
                "Synthesis: %.1fs left at the %s attempt — too little for a "
                "round trip, returning a degraded response instead of being "
                "cut off mid-call (#133).",
                max(budget_s, 0.0),
                attempt,
            )
            return None
        try:
            return self._generate_sentences(prompt_payload, budget_s)
        except Exception as exc:  # noqa: BLE001 - degrade with the verdict intact
            logger.error(
                "Synthesis: %s generation failed after %.1fs of budget — "
                "returning a degraded response",
                attempt,
                budget_s,
                exc_info=exc,
            )
            return None

    def _generate_sentences(
        self, prompt_payload: dict, budget_s: float | None = None
    ) -> list[dict]:
        client = self._llm_client or self._build_llm_client()
        contents = (
            f"{_SYNTHESIS_SYSTEM_PROMPT}\n\nAgent outputs and language:\n"
            f"{json.dumps(prompt_payload, indent=2)}"
        )
        config: dict[str, Any] = {
            "response_mime_type": "application/json",
            # #137 — see _THINKING_LEVEL for the measurements behind this.
            "thinking_config": {"thinking_level": _THINKING_LEVEL},
        }
        if budget_s is not None:
            # #133: without this the SDK's own (very long) default applies and a
            # stalled call runs until the graph kills compose(). Milliseconds —
            # see google.genai.types.HttpOptions.timeout — and never below the
            # API's own minimum, which it rejects with a 400 (#135).
            deadline_s = max(budget_s, _MIN_API_DEADLINE_S)
            config["http_options"] = {"timeout": int(deadline_s * 1000)}
        response = client.models.generate_content(
            model=_GEMINI_MODEL,
            contents=contents,
            config=config,
            # NOTE: no `temperature` — see module docstring MODEL NOTE.
        )
        data = json.loads(response.text)
        return list(data.get("sentences", []))

    def _response_is_safe(
        self, sentences: list[dict], available_results: dict[str, object]
    ) -> bool:
        """Combines the citation-coverage check (FR-SYN-2) with the
        alerts-availability phrasing check (NFR-REL-1) below — both must
        pass before a response ships."""
        return (
            self._citation_coverage_ok(sentences, available_results)
            and self._alerts_unavailable_phrased_safely(sentences, available_results)
            and self._verdict_phrased_safely(sentences, available_results)
        )

    def _verdict_phrased_safely(
        self, sentences: list[dict], available_results: dict[str, object]
    ) -> bool:
        """FR-RISK-2 / NFR-REL-2 / #39 regression guard: an UNSAFE or
        INSUFFICIENT_DATA verdict must survive composition intact.

        The decision tree goes to real trouble to make these verdicts
        non-negotiable (RiskSafetyAgent, LLD §4.2 / Figure 2), and all of
        that is undone if the sentence a fisherman actually hears is "but
        conditions look fine". Rules 2/2b tell the LLM this; as with the
        citation-coverage and alerts checks, we do not trust it blindly.

        Deterministic and narrow on purpose — it rejects two specific
        failures rather than trying to judge tone:
          1. a risk_safety sentence that reads as reassurance, and
          2. an output that never states the verdict at all.
        Favourable weather/ocean sentences are untouched: reporting a calm
        sea is fine, presenting it as a reason to discount the verdict is
        not, and only risk_safety-sourced sentences can do the latter.
        """
        risk = available_results.get("risk_safety")
        if risk is None:
            return True
        verdict = getattr(risk, "verdict", None)
        if verdict is None and isinstance(risk, dict):
            verdict = risk.get("verdict")
        if verdict not in ("UNSAFE", "INSUFFICIENT_DATA"):
            return True

        reassurance = (
            "safe to", "should be fine", "looks fine", "look fine", "no risk",
            "no danger", "conditions are good", "conditions are fine", "all clear",
            "you can proceed", "good to go", "no concern",
        )
        risk_sentences = [s for s in sentences if s.get("source") == "risk_safety"]
        for sentence in risk_sentences:
            text = (sentence.get("text") or "").lower()
            if any(_contains_phrase(text, phrase) for phrase in reassurance):
                logger.warning(
                    "Synthesis: risk_safety sentence reads as reassurance while "
                    "the verdict is %s: %r", verdict, sentence.get("text"),
                )
                return False

        # The verdict has to actually appear somewhere. A response that
        # simply omits an UNSAFE verdict is as dangerous as one that
        # contradicts it, and is the likelier LLM failure of the two.
        spoken = {
            "UNSAFE": ("unsafe", "not safe", "do not go", "don't go", "avoid"),
            "INSUFFICIENT_DATA": (
                "insufficient", "not enough data", "cannot be given", "can't be given",
                "unavailable", "cannot determine", "can't determine", "unknown",
            ),
        }[verdict]
        if not any(
            any(_contains_phrase((s.get("text") or "").lower(), phrase) for phrase in spoken)
            for s in sentences
        ):
            logger.warning(
                "Synthesis: verdict is %s but no sentence states it.", verdict
            )
            return False
        return True

    def _alerts_unavailable_phrased_safely(
        self, sentences: list[dict], available_results: dict[str, object]
    ) -> bool:
        """NFR-REL-1 / #37 regression guard: when
        weather.alerts_source_available is False, active_alerts == [] means
        "unknown", not "clear" (see WeatherResult and RiskSafetyAgent
        docstrings). Deterministically reject any weather-sourced sentence
        that reads as an all-clear, and require at least one that flags the
        alert data as unavailable — the LLM is told this in the prompt (rule
        2a) but this check does not trust it blindly, same rationale as the
        citation-coverage check not trusting the LLM's citations blindly."""
        weather = available_results.get("weather")
        if weather is None or getattr(weather, "alerts_source_available", True):
            return True

        false_reassurance = ("no active alert", "no alert", "clear", "safe to", "all clear")
        unavailable_phrasing = ("unavailable", "cannot be confirmed", "can't be confirmed",
                                 "could not be checked", "couldn't be checked", "unknown")

        weather_sentences = [s for s in sentences if s.get("source") == "weather"]
        flagged_unavailable = False
        for sentence in weather_sentences:
            text = (sentence.get("text") or "").lower()
            if any(phrase in text for phrase in false_reassurance) and not any(
                phrase in text for phrase in unavailable_phrasing
            ):
                logger.warning(
                    "Synthesis: weather sentence reads as all-clear while "
                    "alerts_source_available is False: %r", sentence.get("text"),
                )
                return False
            if any(phrase in text for phrase in unavailable_phrasing):
                flagged_unavailable = True

        if not flagged_unavailable:
            logger.warning(
                "Synthesis: alerts_source_available is False but no weather "
                "sentence flags alert data as unavailable."
            )
            return False
        return True

    def _citation_coverage_ok(
        self, sentences: list[dict], available_results: dict[str, object]
    ) -> bool:
        """FR-SYN-2 safety check. Every sentence must have a non-empty
        `source` that is either "none" or a key present in
        available_results — never fabricated or missing."""
        if not sentences:
            return False
        valid_sources = set(available_results.keys()) | {"none"}
        for sentence in sentences:
            source = sentence.get("source")
            if not source or source not in valid_sources:
                logger.warning(
                    "Synthesis: uncited/invalid-source sentence: %r "
                    "(source=%r, valid=%r)",
                    sentence.get("text"), source, valid_sources,
                )
                return False
        return True

    def _build_citations(
        self, sentences: list[dict], available_results: dict[str, object]
    ) -> list[Citation]:
        """One Citation per distinct agent actually cited — never
        fabricated (NFR-REL-1 extended to citations)."""
        cited_agents = {s["source"] for s in sentences if s.get("source") != "none"}
        citations = []
        for agent_name in cited_agents:
            result = available_results.get(agent_name)
            if result is None:
                continue
            citations.append(Citation(source=agent_name, timestamp=_extract_data_timestamp(result)))
        return citations

    # ------------------------------------------------------------------ #
    # Composition without the LLM (#139)
    # ------------------------------------------------------------------ #
    def _compose_without_llm(
        self, available_results: dict[str, object], plan: ExecutionPlan
    ) -> ComposedResponse:
        """A real answer, built from the agent outputs, when the LLM couldn't
        give us one.

        The model's job here was only ever phrasing — every fact was computed
        and checked before Synthesis was called, and Risk's rationale is
        already a complete human-readable sentence. So a slow or failing Gemini
        call is no reason to tell a fisherman we have nothing; it is a reason
        to say it less fluently.

        verified=True, deliberately. These sentences are generated FROM the
        agent fields and tagged with the agent they came from, so citation
        coverage holds by construction and there is nothing for the model to
        hallucinate — #121's "don't render a verdict we can't explain" is
        satisfied, and the verdict finally reaches the user on this path.
        Which is the whole point: a CAUTION computed from real data used to be
        withheld purely because only an LLM sentence could carry it.

        Runs through the same _response_is_safe() gate as the model's output
        rather than trusting itself. It should pass by construction; if it ever
        doesn't, that is a bug in the sentence builders and the apology is
        still there to catch it.
        """
        sentences = _deterministic_sentences(available_results)
        if not sentences or not self._response_is_safe(sentences, available_results):
            logger.error(
                "Synthesis: deterministically-composed response failed its own "
                "safety checks — this is a bug in the sentence builders, not a "
                "model failure. Falling back to the apology.",
            )
            return self._degraded_response(available_results, plan)

        logger.info(
            "Synthesis: composed %d sentences without the LLM (#139).", len(sentences)
        )
        return ComposedResponse(
            text=" ".join(s["text"] for s in sentences),
            citations=self._build_citations(sentences, available_results),
            map_payload=MapPayload(),
            trace=list(plan.trace),
            verified=True,
        )

    def _degraded_response(
        self, available_results: dict[str, object], plan: ExecutionPlan
    ) -> ComposedResponse:
        """Last resort — no available data, no budget left, a failed LLM call,
        or citation coverage failed twice. States only the verdict (if present
        and if naming it cannot reassure) and never ships an unverified
        sentence to a fisherman.

        Returns verified=False so the Gateway withholds the verdict badge
        (#121/#133): the text below explains that nothing could be verified,
        and a badge above it saying otherwise would contradict it.
        """
        risk = available_results.get("risk_safety")
        verdict = getattr(risk, "verdict", None) if risk is not None else None
        # A CAUTION/UNSAFE/INSUFFICIENT_DATA verdict is a warning, and repeating
        # a warning we could not fully explain is the conservative move. SAFE is
        # reassurance, and "I have a SAFE assessment ..." on a response that
        # verified nothing is precisely the claim this system exists not to make
        # (NFR-REL-2) — so it falls through to the neutral wording below.
        if verdict and verdict != "SAFE":
            text = (
                f"I have a {verdict} assessment for this query, but couldn't "
                "generate a fully verified explanation right now. Please "
                "treat this as a caution and check conditions before proceeding."
            )
        elif available_results:
            text = (
                "I wasn't able to generate a fully verified response right now. "
                "Please try rephrasing, or check conditions directly with "
                "INCOIS/IMD before making a decision."
            )
        else:
            text = (
                "I couldn't retrieve the data needed to answer that for this "
                "location right now. Please try again shortly."
            )
        return ComposedResponse(
            text=text,
            citations=[],
            map_payload=MapPayload(),
            trace=list(plan.trace),
            verified=False,
        )

    def _build_llm_client(self):
        """Shared across every SynthesisAgent instance (#149) — same reason as
        PlannerAgent's: the graph default-constructs a fresh agent per build,
        so a per-instance client meant a new httpx connection pool, and a fresh
        DNS + TCP + TLS handshake, on every query."""
        global _SHARED_LLM_CLIENT
        with _SHARED_LLM_CLIENT_LOCK:
            if _SHARED_LLM_CLIENT is not None:
                return _SHARED_LLM_CLIENT
            _SHARED_LLM_CLIENT = self._construct_llm_client()
            return _SHARED_LLM_CLIENT

    def _construct_llm_client(self):
        from google import genai

        settings = get_settings()
        return genai.Client(api_key=settings.llm_api_key)