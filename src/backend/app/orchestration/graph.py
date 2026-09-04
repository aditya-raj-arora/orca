"""
LangGraph state-graph wiring: Planner -> {Weather, Ocean, Geofencing} -> Risk ->
Synthesis.

Owner: P1 (Backend/Orchestration Lead).
Reference: HLD v1.0 §2.3 ("Orchestration Layer ... implemented as a LangGraph
state graph"), LLD v1.0 §2.2, §4.1/§4.2 for the two decision trees this graph
executes (Planner routing, Risk verdict combination).

Why LangGraph (not a plain function pipeline): the explicit state-graph model
makes the Planner -> Agents -> Synthesis flow inspectable at each node, which
directly powers the agent-trace requirement (FR-PLAN-4, FR-UI-3) — see HLD §6
tech-stack rationale table.

DESIGN NOTE — always-run fan-out, not conditional fan-out: weather/ocean/
geofencing are wired as unconditional edges out of "planner" (LangGraph runs
all three in parallel automatically); each node function internally no-ops
(returns {} — no state change) if the Planner didn't request that agent, or
if the Planner asked for a clarifying question instead. This was chosen over
LangGraph's conditional-edge / Send-based dynamic fan-out because it doesn't
depend on a specific LangGraph version's fan-out semantics, is trivial to
unit-test (assert the "skipped" node's result key is absent), and the no-op
cost for an unrequested agent is a single dict lookup — negligible next to
NFR-PERF's multi-second budgets.
"""
from __future__ import annotations

import asyncio
import logging
import operator
import re
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, StateGraph

from app.agents.geofencing_agent import GeofencingAgent
from app.agents.ocean_agent import OceanAgent
from app.agents.risk_safety_agent import RiskSafetyAgent
from app.agents.weather_agent import WeatherAgent
from app.core.session import ConversationContext
from app.data_access.gis_boundary_adapter import GISBoundaryAdapter
from app.data_access.incois_adapter import INCOISAdapter
from app.data_access.weather_adapter import WeatherDataAdapter
from app.orchestration.planner_agent import NormalizedQuery, PlannerAgent
from app.orchestration.synthesis_agent import SynthesisAgent
from app.schemas.common import LatLon, TimeWindow
from app.schemas.synthesis import ComposedResponse, ExecutionPlan

logger = logging.getLogger(__name__)

# NFR-PERF-1/2: 8s single-agent / 15s multi-agent response budgets (SRS
# §5.1). Each specialist agent gets a bounded slice of that so one slow or
# stuck external API can't blow the whole query's budget — LLD §6 "Partial
# agent timeout" row: a timed-out agent is treated identically to an errored
# one, never left to hang the query.
AGENT_TIMEOUT_SECONDS = 6.0

# Synthesis gets its own, larger budget (#118). AGENT_TIMEOUT_SECONDS is sized
# for a specialist making one bounded HTTP call; SynthesisAgent.compose() makes
# up to TWO sequential Gemini round trips — the initial generation plus a full
# regeneration when the citation-coverage / phrasing safety checks reject the
# first (synthesis_agent.py). At 6s the regeneration had almost no budget left,
# so a tripped safety check became a timeout and the user got the degraded
# "couldn't put together an answer" string instead of a real response.
#
# Deliberately a separate constant rather than raising AGENT_TIMEOUT_SECONDS:
# 6s is correct for the specialists, and loosening it would let one slow
# external API eat the whole query budget, which is precisely what that
# constant exists to prevent.
SYNTHESIS_TIMEOUT_SECONDS = 10.0


def _merge_dicts(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Reducer for GraphState.results: the three specialist nodes run in
    parallel and each write their own key, so results must be merged rather
    than overwritten (LangGraph's default per-key state update is last-write-
    wins, which would silently drop concurrent writes without this)."""
    return {**a, **b}


class GraphState(TypedDict, total=False):
    query: NormalizedQuery
    context: ConversationContext
    language: str
    plan: ExecutionPlan
    results: Annotated[dict[str, Any], _merge_dicts]
    composed: ComposedResponse
    # False when Synthesis degraded to the _unavailable_composed_response()
    # sentinel (#121). The Gateway must not render a verdict badge for a
    # response it could not explain — see main._final_response(). Internal to
    # the graph, deliberately not on the ComposedResponse contract, so this
    # needs no contract-change broadcast (CONTRIBUTING.md §6).
    synthesis_ok: bool
    # Human-readable step log for FR-PLAN-4 / FR-UI-3 (the WebSocket
    # trace_update stream, LLD §5.2, is built by replaying this list).
    trace: Annotated[list[str], operator.add]


def _agent_requested(state: GraphState, agent_name: str) -> bool:
    plan = state.get("plan")
    if plan is None or plan.needs_clarification:
        return False
    return any(inv.agent_name == agent_name for inv in plan.invocations)


async def _call_bounded(
    fn: Any,
    *args: Any,
    unavailable: Any,
    agent_label: str,
    timeout: float | None = None,
) -> tuple[Any, str]:
    """Runs a (synchronous, per the LLD's agent method signatures) agent call
    in a worker thread, bounded by `timeout` (AGENT_TIMEOUT_SECONDS unless the
    caller overrides it — see SYNTHESIS_TIMEOUT_SECONDS). Returns
    (result_or_unavailable, trace_line) — never raises, per LLD §6: a failed
    or timed-out agent must degrade to an 'unavailable' result, not crash the
    query or the whole orchestration graph.

    `timeout=None` resolves to AGENT_TIMEOUT_SECONDS *at call time*, not as a
    default-argument value: a default would bind the module global once at
    import, and tests/integration/test_failure_matrix.py patches that global
    down to make the timeout row run fast. Binding it early silently disabled
    that patch."""
    if timeout is None:
        timeout = AGENT_TIMEOUT_SECONDS
    try:
        result = await asyncio.wait_for(asyncio.to_thread(fn, *args), timeout=timeout)
        return result, f"{agent_label}: data received"
    except TimeoutError:
        return unavailable, f"{agent_label}: timed out after {timeout}s (unavailable)"
    except Exception as exc:  # noqa: BLE001 - deliberate: degrade, don't crash the query
        return unavailable, f"{agent_label}: error ({exc}) — treated as unavailable"


# ---------------------------------------------------------------------- #
# Node factories — each returns an async node function closing over its
# agent instance, so build_orchestration_graph() can inject fakes for tests
# without any LLM key / DB / external API required.
# ---------------------------------------------------------------------- #


def _planner_node(planner: PlannerAgent):
    async def _node(state: GraphState) -> dict[str, Any]:
        plan = planner.plan(state["query"], state["context"])
        return {"plan": plan, "trace": list(plan.trace)}

    return _node


def _weather_node(agent: WeatherAgent):
    async def _node(state: GraphState) -> dict[str, Any]:
        if not _agent_requested(state, "weather"):
            return {}
        location = _location_for(state, "weather")
        if location is None:
            return {
                "results": {"weather": _unavailable_weather_result()},
                "trace": ["Weather Agent: location not resolved to coordinates (unavailable)"],
            }
        window = _time_window_for(state, "weather")
        result, trace_line = await _call_bounded(
            agent.get_conditions,
            location,
            window,
            unavailable=_unavailable_weather_result(),
            agent_label="Weather Agent",
        )
        return {"results": {"weather": result}, "trace": [trace_line]}

    return _node


def _ocean_node(agent: OceanAgent):
    async def _node(state: GraphState) -> dict[str, Any]:
        if not _agent_requested(state, "ocean"):
            return {}
        location = _location_for(state, "ocean")
        if location is None:
            return {
                "results": {"ocean": None, "ocean_params": None},
                "trace": ["Ocean Agent: location not resolved to coordinates (unavailable)"],
            }
        # PFZ (Risk-relevant, FR-OCEAN-1/3/4) and SST/chlorophyll
        # (descriptive only, FR-OCEAN-2 — not consumed by Risk/Safety's
        # Figure 2 tree, only by Synthesis) are two independent calls on the
        # same agent instance; run them concurrently rather than serially so
        # one "ocean" node stays within AGENT_TIMEOUT_SECONDS like every
        # other specialist, not double it.
        (pfz_result, pfz_trace), (params_result, params_trace) = await asyncio.gather(
            _call_bounded(
                agent.get_nearest_pfz,
                location,
                unavailable=None,
                agent_label="Ocean Agent (PFZ)",
            ),
            _call_bounded(
                agent.get_ocean_parameters,
                location,
                unavailable=_unavailable_ocean_params(),
                agent_label="Ocean Agent (SST/chlorophyll)",
            ),
        )
        return {
            "results": {"ocean": pfz_result, "ocean_params": params_result},
            "trace": [pfz_trace, params_trace],
        }

    return _node


def _geofencing_node(agent: GeofencingAgent):
    async def _node(state: GraphState) -> dict[str, Any]:
        if not _agent_requested(state, "geofencing"):
            return {}
        location = _location_for(state, "geofencing")
        if location is None:
            return {
                "results": {"geofencing": None},
                "trace": ["Geofencing Agent: location not resolved to coordinates (unavailable)"],
            }
        result, trace_line = await _call_bounded(
            agent.check,
            location,
            unavailable=None,
            agent_label="Geofencing Agent",
        )
        return {"results": {"geofencing": result}, "trace": [trace_line]}

    return _node


def _risk_node(agent: RiskSafetyAgent):
    async def _node(state: GraphState) -> dict[str, Any]:
        plan = state.get("plan")
        if plan is None or plan.needs_clarification:
            return {}
        if not any(inv.agent_name == "risk_safety" for inv in plan.invocations):
            return {}
        results = state.get("results", {})
        verdict, trace_line = await _call_bounded(
            agent.evaluate,
            results.get("weather"),
            results.get("geofencing"),
            results.get("ocean"),
            unavailable=None,
            agent_label="Risk/Safety Agent",
        )
        return {"results": {"risk_safety": verdict}, "trace": [trace_line]}

    return _node


def _synthesis_node(agent: SynthesisAgent):
    async def _node(state: GraphState) -> dict[str, Any]:
        plan = state.get("plan")
        if plan is not None and plan.needs_clarification:
            # No agents ran, nothing to synthesize from — the clarification
            # prompt IS the response; skip the LLM call entirely (LLD §2.2).
            composed = ComposedResponse(text=plan.clarification_prompt or "")
            # A clarifying question IS a successful response — the LLM was
            # skipped deliberately, nothing failed.
            return {
                "composed": composed,
                "synthesis_ok": True,
                "trace": ["Synthesis: skipped (clarification requested)"],
            }

        sentinel = _unavailable_composed_response()
        composed, trace_line = await _call_bounded(
            agent.compose,
            plan,
            state.get("results", {}),
            state.get("language", "en"),
            unavailable=sentinel,
            agent_label="Synthesis Agent",
            timeout=SYNTHESIS_TIMEOUT_SECONDS,
        )
        return {
            "composed": composed,
            "synthesis_ok": composed is not sentinel,
            "trace": [trace_line],
        }

    return _node


def _location_for(state: GraphState, agent_name: str) -> LatLon | None:
    """Converts the Planner's plain `location` dict (LLD §2.2's ExecutionPlan
    payload: {"place_name", "lat", "lon"} — planner_agent.py's
    QueryEntities.as_location_dict()) into the LatLon each specialist agent
    expects (LLD §2.3-2.5 signatures). Single seam so the conversion logic
    isn't duplicated across three nodes.

    Resolved at the P1 contract-lock sync (2026-09-01): lat/lon can be None
    even when location_resolvable is True (the Planner's entity-extraction
    LLM knows a place name but not its coordinates — there's no geocoding
    step yet, tracked separately). That must degrade the same way an
    unavailable agent result does, not raise — callers check for None."""
    plan = state.get("plan")
    if plan is None:
        return None
    for inv in plan.invocations:
        if inv.agent_name == agent_name:
            loc = inv.input_payload.get("location") or {}
            lat, lon = loc.get("lat"), loc.get("lon")
            if lat is None or lon is None:
                return None
            return LatLon(lat=lat, lon=lon)
    return None


def _time_window_for(state: GraphState, agent_name: str) -> TimeWindow | None:
    """Same seam as _location_for, for the Planner's free-text
    time_window_text (only ever set on the weather invocation's payload —
    see planner_agent.route_query)."""
    plan = state.get("plan")
    if plan is None:
        return None
    for inv in plan.invocations:
        if inv.agent_name == agent_name:
            return _resolve_time_window(inv.input_payload.get("time_window_text"))
    return None


_DAY_PART_HOURS = {
    "morning": (6, 12),
    "afternoon": (12, 17),
    "evening": (17, 21),
    "night": (21, 30),  # end > 24 -> wraps into the next day, handled below
}


def _resolve_time_window(text: str | None, now: datetime | None = None) -> TimeWindow | None:
    """Planner's time_window_text (free-text, e.g. "tomorrow morning") ->
    a concrete UTC TimeWindow for the Weather Agent's forecast-range call.

    Deliberately conservative (P1 contract-lock decision, 2026-09-01): only
    recognises today/tomorrow/day-after-tomorrow plus an optional day-part
    (morning/afternoon/evening/night). Anything else (weekday names, "next
    week", open-ended ranges) -> None, so the agent falls back to current
    conditions rather than guessing a window — the "never fabricate" rule
    (FR-WX-4) extends to date ranges, not just data values. Pure, no I/O —
    unit-tested directly against sample phrases.
    """
    if not text:
        return None
    t = text.strip().lower()
    now = (now or datetime.now(UTC)).replace(minute=0, second=0, microsecond=0)

    if re.search(r"\bday after tomorrow\b", t):
        day_offset = 2
    elif re.search(r"\btomorrow\b", t):
        day_offset = 1
    elif re.search(r"\b(today|now|right now|currently|tonight)\b", t):
        day_offset = 0
    else:
        return None

    base = now + timedelta(days=day_offset)

    for part, (start_h, end_h) in _DAY_PART_HOURS.items():
        if re.search(rf"\b{part}\b", t):
            start = base.replace(hour=start_h % 24)
            end = base.replace(hour=end_h % 24) + (
                timedelta(days=1) if end_h >= 24 else timedelta(0)
            )
            return TimeWindow(start=start, end=end)

    start = base.replace(hour=0)
    return TimeWindow(start=start, end=start + timedelta(days=1))


def _unavailable_weather_result() -> Any:
    from app.schemas.weather import WeatherResult

    # alerts_source_available MUST be False here (#110). It defaults to True,
    # which would claim both alert feeds were reached and found clear while
    # this result carries wind=0.0/wave=0.0 — exactly the "empty list read as
    # 'no alerts' rather than 'unknown'" trap WeatherResult's own docstring
    # warns about (NFR-REL-2). WeatherAgent._unavailable() sets it False for
    # the same reason; this sentinel must not disagree with it.
    return WeatherResult(
        wind_speed_kmh=0.0,
        wave_height_m=0.0,
        status="unavailable",
        alerts_source_available=False,
    )


def _unavailable_ocean_params() -> Any:
    from app.schemas.ocean import OceanParams

    # OceanParams has no status field (FR-OCEAN-2) — unavailability is
    # represented by None fields, same convention OceanAgent.get_ocean_
    # parameters() itself uses when INCOIS doesn't publish a value.
    return OceanParams(sea_surface_temp_c=None, chlorophyll_mg_m3=None)


def _unavailable_composed_response() -> ComposedResponse:
    """SynthesisAgent.compose() raising or timing out (e.g. NotImplementedError
    while #14/#9 are still in flight, or a real LLM failure later) must not
    crash the whole query — same "degrade, don't crash" rule _call_bounded
    already applies to every other node. No fabricated claims, no citations
    (there's nothing to cite), and the wording doesn't imply an answer was
    given (contrast with a genuine INSUFFICIENT_DATA verdict, which IS an
    answer)."""
    return ComposedResponse(
        text="Sorry, I couldn't put together an answer for that just now — please try again."
    )


# ---------------------------------------------------------------------- #
# Public entry point
# ---------------------------------------------------------------------- #


def build_orchestration_graph(
    planner: PlannerAgent | None = None,
    weather_agent: WeatherAgent | None = None,
    ocean_agent: OceanAgent | None = None,
    geofencing_agent: GeofencingAgent | None = None,
    risk_agent: RiskSafetyAgent | None = None,
    synthesis_agent: SynthesisAgent | None = None,
):
    """Builds and compiles the LangGraph state graph. All agent params are
    optional and default-constructed with their real adapters when omitted —
    tests inject fakes here instead of needing a full environment (LLM keys,
    DB, live external APIs) to exercise the orchestration wiring itself; see
    tests/test_graph.py."""
    planner = planner or PlannerAgent()
    weather_agent = weather_agent or WeatherAgent(WeatherDataAdapter())
    ocean_agent = ocean_agent or OceanAgent(INCOISAdapter())
    geofencing_agent = geofencing_agent or GeofencingAgent(GISBoundaryAdapter())
    risk_agent = risk_agent or RiskSafetyAgent()
    synthesis_agent = synthesis_agent or SynthesisAgent()

    graph = StateGraph(GraphState)
    graph.add_node("planner", _planner_node(planner))
    graph.add_node("weather", _weather_node(weather_agent))
    graph.add_node("ocean", _ocean_node(ocean_agent))
    graph.add_node("geofencing", _geofencing_node(geofencing_agent))
    graph.add_node("risk_safety", _risk_node(risk_agent))
    graph.add_node("synthesis", _synthesis_node(synthesis_agent))

    graph.set_entry_point("planner")

    # Fan-out: all three specialist nodes run in parallel; each is a no-op
    # if not requested / clarification was asked for instead (see module
    # docstring "DESIGN NOTE").
    graph.add_edge("planner", "weather")
    graph.add_edge("planner", "ocean")
    graph.add_edge("planner", "geofencing")

    # Fan-in: risk_safety only proceeds once all three incoming edges have
    # completed (LangGraph waits for every predecessor by default).
    graph.add_edge("weather", "risk_safety")
    graph.add_edge("ocean", "risk_safety")
    graph.add_edge("geofencing", "risk_safety")

    graph.add_edge("risk_safety", "synthesis")
    graph.add_edge("synthesis", END)

    return graph.compile()


async def run_query(
    query: NormalizedQuery, context: ConversationContext, language: str = "en", **agents: Any
) -> GraphState:
    """Convenience wrapper for the Gateway (app/main.py) — builds (or reuses,
    TODO(P1): cache the compiled graph rather than rebuilding per request
    once this is wired into main.py for real) the graph and runs one query
    through it end-to-end. `**agents` forwards any of build_orchestration_
    graph()'s injection params, for tests."""
    compiled = build_orchestration_graph(**agents)
    initial_state: GraphState = {"query": query, "context": context, "language": language}
    return await compiled.ainvoke(initial_state)
