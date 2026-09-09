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

MODEL NOTE: uses gemini-3.6-flash, not gemini-2.5-flash (retired for new
API keys — see Issue #<N>, also affects planner_agent.py). No `temperature`
param passed — Gemini 3.x migration notes say sampling params are
unsupported on 3.x models.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime

from app.core.config import get_settings
from app.schemas.common import Citation
from app.schemas.synthesis import ComposedResponse, ExecutionPlan, MapPayload

logger = logging.getLogger(__name__)

_GEMINI_MODEL = "gemini-3.6-flash"

_SYNTHESIS_SYSTEM_PROMPT = """You are the response-composition step of a marine \
safety assistant. You will be given the outputs of one or more specialist \
agents (weather, ocean, ocean_params, geofencing, ocean_nearby, \
geofencing_nearby, risk_safety) as JSON, plus the target response language.

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
5. "ocean_nearby" and "geofencing_nearby", when present, each carry a LIST of \
zones near the queried point (the "zones" / "mpas" arrays) — this is the \
answer to a "which zones / which regions" question. Enumerate the zones: name \
or id, distance, and bearing where given. If the list is empty, say plainly \
that no such zones were found within the search radius given ("radius_km") — \
never phrase it as though a wider area was surveyed and found clear. Only one \
point was actually evaluated plus this radius scan; do not imply a full \
regional survey.

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

    return json.loads(json.dumps(data, default=_default))


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
    def __init__(self, llm_client: object | None = None) -> None:
        # Same lazy-construction pattern as PlannerAgent — unit tests never
        # need a real API key.
        self._llm_client = llm_client

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

        sentences = self._generate_sentences(prompt_payload)

        if not self._response_is_safe(sentences, available_results):
            logger.warning(
                "Synthesis: safety check failed on first attempt — "
                "regenerating once (FR-SYN-2 / NFR-REL-1)."
            )
            sentences = self._generate_sentences(prompt_payload)

        if not self._response_is_safe(sentences, available_results):
            logger.error(
                "Synthesis: safety check failed twice — refusing to ship an "
                "uncited or falsely-reassuring claim. Returning degraded response."
            )
            return self._degraded_response(available_results, plan)

        text = " ".join(s["text"] for s in sentences)
        citations = self._build_citations(sentences, available_results)

        return ComposedResponse(
            text=text,
            citations=citations,
            # TODO(P2, Issue #14): populate from geofence/ocean results once
            # real agents land — coordinate exact marker/zone shape with P6
            # (owns Leaflet rendering, see MapPayload TODO).
            map_payload=MapPayload(),
            trace=list(plan.trace),
        )

    def _generate_sentences(self, prompt_payload: dict) -> list[dict]:
        client = self._llm_client or self._build_llm_client()
        contents = (
            f"{_SYNTHESIS_SYSTEM_PROMPT}\n\nAgent outputs and language:\n"
            f"{json.dumps(prompt_payload, indent=2)}"
        )
        response = client.models.generate_content(
            model=_GEMINI_MODEL,
            contents=contents,
            config={"response_mime_type": "application/json"},
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

    def _degraded_response(
        self, available_results: dict[str, object], plan: ExecutionPlan
    ) -> ComposedResponse:
        """Last resort — no available data, or citation coverage failed
        twice. States only the verdict (if present) and never ships an
        unverified sentence to a fisherman."""
        risk = available_results.get("risk_safety")
        verdict = getattr(risk, "verdict", None) if risk is not None else None
        if verdict:
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
            text=text, citations=[], map_payload=MapPayload(), trace=list(plan.trace)
        )

    def _build_llm_client(self):
        from google import genai

        settings = get_settings()
        return genai.Client(api_key=settings.llm_api_key)