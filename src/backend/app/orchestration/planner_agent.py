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
from typing import TYPE_CHECKING

from app.core.config import get_settings
from app.core.session import ConversationContext
from app.schemas.synthesis import AgentInvocationRequest, ExecutionPlan

if TYPE_CHECKING:  # type-only: keeps this module importable without data_access
    from app.data_access.base import DataSourceAdapter

logger = logging.getLogger(__name__)

# LLD §2.2: "Low-confidence extractions trigger a clarifying question rather
# than a guess." Threshold is a plain constant, not hardcoded inline, so it's
# one place to tune once real queries are being tested against it.
MIN_ENTITY_CONFIDENCE = 0.5

# Confidence reported by the LLM-down keyword fallback (_extract_via_keywords).
# Above MIN_ENTITY_CONFIDENCE when at least one intent keyword matched, below
# it when none did — see that method's docstring for why this is derived
# rather than fixed.
_KEYWORD_FALLBACK_CONFIDENCE = 0.55
_NO_MATCH_CONFIDENCE = 0.2

# google-genai model id. Free tier via Google AI Studio — see
# docs/CREDENTIALS.md #1 for why Gemini was chosen over Claude/GPT.
# gemini-2.5-flash was retired for new API keys (#51); gemini-3.5-flash-lite
# chosen over gemini-3.6-flash for its higher free-tier RPM/RPD.
_GEMINI_MODEL = "gemini-3.5-flash-lite"

# Bound on the entity-extraction call (#133). Sized under graph's
# AGENT_TIMEOUT_SECONDS: the planner runs before the specialists fan out, so
# time spent here is time taken from every node after it.
_LLM_TIMEOUT_S = 5.0

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

Respond with ONLY a JSON object, no other text, matching this shape:
{"location_resolvable": bool, "place_name": str|null, "lat": float|null, \
"lon": float|null, "time_window_text": str|null, "intent_safety": bool, \
"intent_fishing": bool, "intent_boundary": bool, \
"intent_keywords": [str, ...], "confidence": float}
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
    def __init__(
        self,
        llm_client: object | None = None,
        geocoder: DataSourceAdapter | None = None,
    ) -> None:
        # Both lazily constructed if not injected, so unit tests (and
        # route_query() callers that don't need extraction) never need a real
        # API key or a live geocoding call.
        self._llm_client = llm_client
        self._geocoder: DataSourceAdapter | None = geocoder

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

        location = _usable_location(entities.as_location_dict())
        if location is None and entities.place_name:
            # Figure 1's "Location resolvable?" needs coordinates, not a name.
            # The extraction LLM is only asked for lat/lon "if you know it", so
            # a named place with null coordinates is common — geocode it rather
            # than letting it through as resolved (#110).
            location = self._geocode(entities.place_name, trace)

        if location is None:
            trace.append("Planner: no location in query — checking prior conversation turns")
            # Guarded the same way: a turn recorded before this fix (or by any
            # other writer) could hold a coordinate-less location, and reusing
            # one would silently disable every specialist agent for the rest of
            # the session (FR-PLAN-5).
            location = _usable_location(context.last_known_location())

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
            intent_keywords=list(data.get("intent_keywords", [])),
        )

    # ------------------------------------------------------------------ #
    # Location resolution (#110)
    # ------------------------------------------------------------------ #
    def _geocode(self, place_name: str, trace: list[str]) -> dict | None:
        """place_name -> a location dict with real coordinates, or None.

        None is a first-class outcome, not an error path: the caller then runs
        Figure 1's clarifying question. Never raises — GeocodingAdapter.fetch()
        already honours the LLD §2.9 contract, and the broad guard below covers
        construction failing too (e.g. no network in a unit-test environment),
        because a geocoder problem must degrade the query, not crash it."""
        try:
            geocoder = self._geocoder or self._build_geocoder()
            self._geocoder = geocoder
            result = geocoder.fetch({"place_name": place_name})
        except Exception as exc:  # noqa: BLE001 - degrade, don't crash the query
            logger.warning("Planner: geocoding %r failed: %s", place_name, exc)
            trace.append(f"Planner: geocoding {place_name!r} failed — location unresolved")
            return None

        location = _usable_location(result.data) if result.status == "ok" else None
        if location is None:
            trace.append(
                f"Planner: {place_name!r} could not be resolved to coordinates "
                "(Fig.1 'Location resolvable?' = No)"
            )
            return None
        trace.append(
            f"Planner: geocoded {place_name!r} -> "
            f"({location['lat']}, {location['lon']}) {location.get('country_code') or ''}".rstrip()
        )
        return location

    def _build_geocoder(self) -> DataSourceAdapter:
        # Imported lazily so this module stays importable (and route_query()
        # unit-testable) without the data_access layer being constructible.
        from app.data_access.geocoding_adapter import GeocodingAdapter

        return GeocodingAdapter()

    def _build_llm_client(self):
        # Imported lazily so importing this module never requires google-genai
        # to be installed (e.g. when only running route_query() unit tests).
        from google import genai

        settings = get_settings()
        # #133: the SDK's default timeout is effectively unbounded for our
        # purposes, and this is the one LLM call with no backstop above it —
        # graph._planner_node calls plan() directly, not through _call_bounded,
        # so nothing else would ever stop a stalled request. extract_entities()
        # already treats any failure as "fall back to keyword matching", which
        # is exactly the right response to a timeout.
        return genai.Client(
            api_key=settings.llm_api_key,
            http_options={"timeout": int(_LLM_TIMEOUT_S * 1000)},  # milliseconds
        )

    def _extract_via_keywords(self, query: NormalizedQuery) -> QueryEntities:
        """Degraded-mode fallback (LLD §6): no location resolution (there's no
        cheap non-LLM geocoder wired up — TODO(P1) if this fallback proves to
        matter in practice), simple substring matching for intent flags.

        Confidence is derived from whether anything actually matched, NOT
        fixed (#36 integration fix): a fixed sub-threshold value made this
        whole fallback dead code — plan() would bail to the clarifying
        question before ever reading the intent flags, so the LLD §6 row
        ("falls back to a simpler keyword-matching extraction for intent
        classification; IF LOCATION STILL CANNOT BE RESOLVED, asks the
        clarifying follow-up") could never reach its second clause. A
        keyword hit now clears MIN_ENTITY_CONFIDENCE so a session that
        already has a location (FR-PLAN-5) still gets a real answer with
        the LLM down; no hit at all stays well below it, which is what
        plan()'s low-confidence guard is actually for.

        Deliberately just above the threshold rather than high: this is a
        degraded extraction and the value should read as one."""
        text = query.text.lower()
        safety_kw = ("safe", "safety", "risk", "danger", "go out", "venture")
        fishing_kw = ("fish", "fishing", "pfz", "catch", "zone")
        boundary_kw = ("boundary", "border", "restricted", "protected", "mpa", "imbl")

        def _matches(keywords: tuple[str, ...]) -> list[str]:
            return [kw for kw in keywords if kw in text]

        matched = _matches(safety_kw) + _matches(fishing_kw) + _matches(boundary_kw)

        return QueryEntities(
            location_resolvable=False,
            place_name=None,
            lat=None,
            lon=None,
            time_window_text=None,
            intent_safety=bool(_matches(safety_kw)),
            intent_fishing=bool(_matches(fishing_kw)),
            intent_boundary=bool(_matches(boundary_kw)),
            confidence=_KEYWORD_FALLBACK_CONFIDENCE if matched else _NO_MATCH_CONFIDENCE,
            # Surfaced in the trace (FR-PLAN-4) so a degraded run is visibly
            # degraded rather than silently looking like a normal extraction.
            intent_keywords=matched,
        )


# ---------------------------------------------------------------------- #
# Pure routing function — LLD Fig.1, transcribed node-for-node.
# No LLM, no I/O: takes already-extracted entities + a resolved location and
# returns the ExecutionPlan. Kept as a free function (not a method) so it's
# trivially unit-testable without constructing a PlannerAgent at all.
# ---------------------------------------------------------------------- #


def _usable_location(location: dict | None) -> dict | None:
    """A location is only usable if it carries numeric coordinates (#110).

    The specialist agents need LatLon (LLD §2.3-2.5); graph._location_for()
    returns None without both, and every agent then reports 'unavailable'
    WITHOUT calling its adapter. So a name-only dict must never be treated as
    resolved — it looks like a working plan and produces INSUFFICIENT_DATA for
    every query in the session."""
    if not location:
        return None
    lat, lon = location.get("lat"), location.get("lon")
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None
    if isinstance(lat, bool) or isinstance(lon, bool):  # bool is an int subclass
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return location


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

    if entities.intent_fishing:
        trace.append("Planner: intent involves fishing zone/productivity -> invoking Ocean Agent")
        invocations.append(
            AgentInvocationRequest(agent_name="ocean", input_payload={"location": location})
        )

    # Geofencing runs for a safety question too, not just an explicit boundary
    # one (#112). Figure 2 cannot reach any verdict without a geofence — the
    # Risk agent returns INSUFFICIENT_DATA when it is missing (FR-RISK-3 /
    # NFR-REL-2: a missing contributing agent never degrades to SAFE) — so
    # routing "is it safe to fish near X" without Geofencing made that query
    # permanently unanswerable no matter how good the weather data was.
    #
    # It is also the substantively right answer, not just a wiring fix: you
    # cannot honestly tell someone it is safe to go out without knowing whether
    # the trip crosses the IMBL or an MPA.
    if entities.intent_boundary or entities.intent_safety:
        why = (
            "boundary/restricted zone proximity"
            if entities.intent_boundary
            else "safety (Fig.2 needs a geofence before any verdict)"
        )
        trace.append(f"Planner: intent involves {why} -> invoking Geofencing Agent")
        invocations.append(
            AgentInvocationRequest(agent_name="geofencing", input_payload={"location": location})
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
