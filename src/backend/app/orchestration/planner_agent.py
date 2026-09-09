"""
Planner Agent — query decomposition and routing.

Owner: P1 (Backend/Orchestration Lead).
Implements: FR-PLAN-1 to FR-PLAN-5.
Reference: LLD v1.0 §2.2 (class contract) and §4.1 / Figure 1 — this file is a
direct transcription of Figure 1's flowchart (extracted from
docs/ORCA_LLD_v1.0.docx's embedded diagram; see route_query() below, whose
structure mirrors the diagram node-for-node so a reviewer can diff the two).

DESIGN NOTE (do not violate): routing is a DETERMINISTIC decision tree, not a
free-form LLM decision (LLD §4.1) — see route_query(), which takes no LLM
dependency and is unit-tested against fixture QueryEntities in
tests/test_planner_agent.py. Only entity extraction (location/time/intent)
uses the LLM, in extract_entities() below.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from app.core.config import get_settings
from app.core.session import ConversationContext
from app.schemas.synthesis import AgentInvocationRequest, ExecutionPlan

logger = logging.getLogger(__name__)

# LLD §2.2: "Low-confidence extractions trigger a clarifying question rather
# than a guess." Threshold is a plain constant, not hardcoded inline, so it's
# one place to tune once real queries are being tested against it.
MIN_ENTITY_CONFIDENCE = 0.5

# google-genai model id. Free tier via Google AI Studio — see
# docs/CREDENTIALS.md #1 for why Gemini was chosen over Claude/GPT.
# gemini-2.5-flash was retired for new API keys (#51); gemini-3.5-flash-lite
# chosen over gemini-3.6-flash for its higher free-tier RPM/RPD.
_GEMINI_MODEL = "gemini-3.5-flash-lite"

_ENTITY_EXTRACTION_SYSTEM_PROMPT = """You are the entity-extraction step of a \
marine safety assistant's query planner. Given a user's query (already \
translated to English text), extract:

- Whether a location is mentioned or resolvable to a real place, and if so \
its place name and, if you know it, approximate latitude/longitude.
- A time window, if mentioned (e.g. "tomorrow morning"), as free text — do \
not attempt to compute exact timestamps yourself.
- Three independent yes/no intent classifications, matching the query's \
subject matter (a query can be more than one, or none):
  - intent_safety: does the query ask about safety, risk, or venturing out \
to sea (e.g. "is it safe to fish near X", weather/wave conditions relevant \
to going out)?
  - intent_fishing: does the query ask about fishing zones, productivity, \
or oceanographic conditions (PFZ, SST, chlorophyll)?
  - intent_boundary: does the query ask about maritime boundaries or \
restricted/protected zones?
- A confidence score (0.0-1.0) reflecting how sure you are of this \
extraction overall — lower it if the query is ambiguous or ungrammatical, \
rather than guessing.
- Whether the query asks about a single point/location or about a broader \
AREA — i.e. it wants a LIST or COMPARISON of regions/zones ("which regions \
show...", "which fishing zones should be avoided", "areas where..."). \
Return "area" for the list/compare case, otherwise "point".

Respond with ONLY a JSON object, no other text, matching this shape:
{"location_resolvable": bool, "place_name": str|null, "lat": float|null, \
"lon": float|null, "time_window_text": str|null, "intent_safety": bool, \
"intent_fishing": bool, "intent_boundary": bool, \
"intent_keywords": [str, ...], "scope": "point"|"area", "confidence": float}
"""


@dataclass
class NormalizedQuery:
    text: str
    language: str


@dataclass
class QueryEntities:
    location_resolvable: bool
    place_name: str | None
    lat: float | None
    lon: float | None
    time_window_text: str | None
    intent_safety: bool
    intent_fishing: bool
    intent_boundary: bool
    confidence: float
    # "point" (evaluate one location) or "area" (the query wants a list /
    # comparison of zones — issue #174). route_query() passes this through to
    # the ocean/geofencing invocations; the graph nodes then also call their
    # list-nearby methods, not just the single-point ones.
    scope: str = "point"
    # Raw keywords for the trace/debugging (FR-PLAN-4) — not itself used for
    # routing; the three intent_* booleans above are what route_query() reads.
    intent_keywords: list[str] = field(default_factory=list)

    def as_location_dict(self) -> dict | None:
        """None if no location was resolved, else a plain dict — this is what
        gets stashed on ConversationContext (core/session.py) and passed as
        the `location` param to specialist-agent invocations."""
        if not self.location_resolvable:
            return None
        return {"place_name": self.place_name, "lat": self.lat, "lon": self.lon}


class PlannerAgent:
    def __init__(self, llm_client: object | None = None) -> None:
        # Lazily constructed if not injected, so unit tests (and route_query()
        # callers that don't need extraction) never need a real API key.
        self._llm_client = llm_client

    # ------------------------------------------------------------------ #
    # Public entry point
    # ------------------------------------------------------------------ #

    def plan(self, query: NormalizedQuery, context: ConversationContext) -> ExecutionPlan:
        """Core entry point (LLD Fig.1, full flow). Extracts entities, resolves
        location (falling back to conversation context per FR-PLAN-5), then
        delegates to route_query() for the deterministic routing decision."""
        entities = self.extract_entities(query)
        trace = [
            f"Planner: extracted intent_keywords={entities.intent_keywords}, "
            f"confidence={entities.confidence:.2f}"
        ]

        if entities.confidence < MIN_ENTITY_CONFIDENCE:
            trace.append(
                f"Planner: extraction confidence {entities.confidence:.2f} below "
                f"threshold {MIN_ENTITY_CONFIDENCE} — asking clarifying follow-up "
                "rather than guessing (LLD §2.2)"
            )
            plan = ExecutionPlan(
                trace=trace,
                needs_clarification=True,
                clarification_prompt=(
                    "I couldn't quite understand that — could you rephrase, "
                    "including the location and what you'd like to know?"
                ),
            )
            context.append_turn(query.text, {"location": None})
            return plan

        location = entities.as_location_dict()
        if location is None:
            trace.append("Planner: no location in query — checking prior conversation turns")
            location = context.last_known_location()

        if location is None:
            trace.append(
                "Planner: location not resolvable (Fig.1 'Location resolvable?' = No) "
                "— asking clarifying follow-up"
            )
            plan = ExecutionPlan(
                trace=trace,
                needs_clarification=True,
                clarification_prompt="Which location are you asking about?",
            )
            context.append_turn(query.text, {"location": None})
            return plan

        trace.append(f"Planner: location resolved to {location} — routing to specialist agents")
        plan = route_query(entities, location, trace)
        context.append_turn(
            query.text,
            {
                "location": location,
                "intent_safety": entities.intent_safety,
                "intent_fishing": entities.intent_fishing,
                "intent_boundary": entities.intent_boundary,
            },
        )
        return plan

    # ------------------------------------------------------------------ #
    # LLM-backed entity extraction (the ONLY part of the Planner that calls
    # the LLM — see module docstring)
    # ------------------------------------------------------------------ #

    def extract_entities(self, query: NormalizedQuery) -> QueryEntities:
        """Uses the LLM provider (Gemini structured JSON output, see
        docs/CREDENTIALS.md #1) to extract structured entities from free
        text. On provider failure/timeout, falls back to simple keyword
        matching for intent classification (LLD §6 error-handling table) —
        location resolution has no non-LLM fallback, so a fallback
        extraction always leaves location unresolved and lets plan()'s
        clarifying-question path handle it."""
        try:
            return self._extract_via_llm(query)
        except Exception as exc:  # noqa: BLE001 - deliberate broad catch, see LLD §6
            logger.warning(
                "Planner.extract_entities: LLM extraction failed (%s), "
                "falling back to keyword matching",
                exc,
            )
            return self._extract_via_keywords(query)

    def _extract_via_llm(self, query: NormalizedQuery) -> QueryEntities:
        client = self._llm_client or self._build_llm_client()
        response = client.models.generate_content(
            model=_GEMINI_MODEL,
            contents=f"{_ENTITY_EXTRACTION_SYSTEM_PROMPT}\n\nUser query: {query.text}",
            config={
                "response_mime_type": "application/json",
                # temperature/top_p/top_k are unsupported on Gemini 3.x —
                # the model manages its own sampling now (#51); passing
                # them errors on later model generations.
            },
        )
        data = json.loads(response.text)
        return QueryEntities(
            location_resolvable=bool(data.get("location_resolvable", False)),
            place_name=data.get("place_name"),
            lat=data.get("lat"),
            lon=data.get("lon"),
            time_window_text=data.get("time_window_text"),
            intent_safety=bool(data.get("intent_safety", False)),
            intent_fishing=bool(data.get("intent_fishing", False)),
            intent_boundary=bool(data.get("intent_boundary", False)),
            confidence=float(data.get("confidence", 0.0)),
            scope="area" if data.get("scope") == "area" else "point",
            intent_keywords=list(data.get("intent_keywords", [])),
        )

    def _build_llm_client(self):
        # Imported lazily so importing this module never requires google-genai
        # to be installed (e.g. when only running route_query() unit tests).
        from google import genai

        settings = get_settings()
        return genai.Client(api_key=settings.llm_api_key)

    def _extract_via_keywords(self, query: NormalizedQuery) -> QueryEntities:
        """Degraded-mode fallback (LLD §6): no location resolution (there's no
        cheap non-LLM geocoder wired up — TODO(P1) if this fallback proves to
        matter in practice), simple substring matching for intent flags.
        Confidence is fixed low so plan()'s low-confidence path can still
        catch genuinely bad matches if desired."""
        text = query.text.lower()
        safety_kw = ("safe", "safety", "risk", "danger", "go out", "venture")
        fishing_kw = ("fish", "fishing", "pfz", "catch", "zone")
        boundary_kw = ("boundary", "border", "restricted", "protected", "mpa", "imbl")
        # issue #174: "which regions/zones ..." — asks for a list, not a point.
        area_kw = (
            "which region", "which zone", "which area", "which fishing zone",
            "which zones", "which areas", "list of", "where are the",
            "regions show", "zones should", "areas where", "compare",
        )

        def _hit(keywords: tuple[str, ...]) -> bool:
            return any(kw in text for kw in keywords)

        return QueryEntities(
            location_resolvable=False,
            place_name=None,
            lat=None,
            lon=None,
            time_window_text=None,
            intent_safety=_hit(safety_kw),
            intent_fishing=_hit(fishing_kw),
            intent_boundary=_hit(boundary_kw),
            confidence=0.4,
            scope="area" if _hit(area_kw) else "point",
            intent_keywords=[],
        )


# ---------------------------------------------------------------------- #
# Pure routing function — LLD Fig.1, transcribed node-for-node.
# No LLM, no I/O: takes already-extracted entities + a resolved location and
# returns the ExecutionPlan. Kept as a free function (not a method) so it's
# trivially unit-testable without constructing a PlannerAgent at all.
# ---------------------------------------------------------------------- #


def route_query(entities: QueryEntities, location: dict, trace: list[str]) -> ExecutionPlan:
    """Figure 1, from the "Intent involves safety / venturing out to sea?"
    diamond onward (location resolution already happened in plan()):

      - intent_safety?    -> invoke Weather Agent
      - intent_fishing?   -> invoke Ocean Agent
      - intent_boundary?  -> invoke Geofencing Agent
      - any of the above invoked? -> invoke Risk/Safety Agent with all
        available outputs; otherwise this was an informational-only query.
    """
    invocations: list[AgentInvocationRequest] = []

    if entities.intent_safety:
        trace.append("Planner: intent involves safety/venturing to sea -> invoking Weather Agent")
        invocations.append(
            AgentInvocationRequest(
                agent_name="weather",
                input_payload={"location": location, "time_window_text": entities.time_window_text},
            )
        )

    if entities.scope == "area":
        trace.append(
            "Planner: query is area-scoped (asks which zones/regions, not one "
            "point) -> ocean/geofencing agents will also list nearby zones (issue #174)"
        )

    if entities.intent_fishing:
        trace.append("Planner: intent involves fishing zone/productivity -> invoking Ocean Agent")
        invocations.append(
            AgentInvocationRequest(
                agent_name="ocean",
                input_payload={"location": location, "scope": entities.scope},
            )
        )

    if entities.intent_boundary:
        trace.append(
            "Planner: intent involves boundary/restricted zone proximity "
            "-> invoking Geofencing Agent"
        )
        invocations.append(
            AgentInvocationRequest(
                agent_name="geofencing",
                input_payload={"location": location, "scope": entities.scope},
            )
        )

    if invocations:
        depends_on = [inv.agent_name for inv in invocations]
        trace.append(
            f"Planner: {depends_on} invoked -> invoking Risk/Safety Agent "
            "with all available outputs"
        )
        invocations.append(
            AgentInvocationRequest(
                agent_name="risk_safety", input_payload={"depends_on": depends_on}
            )
        )
    else:
        trace.append(
            "Planner: no safety/fishing/boundary intent detected -> informational query only, "
            "Risk/Safety Agent not invoked"
        )

    return ExecutionPlan(invocations=invocations, trace=trace)
