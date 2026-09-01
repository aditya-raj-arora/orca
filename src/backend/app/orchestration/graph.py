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
from app.schemas.synthesis import ComposedResponse, ExecutionPlan

logger = logging.getLogger(__name__)

# NFR-PERF-1/2: 8s single-agent / 15s multi-agent response budgets (SRS
# §5.1). Each specialist agent gets a bounded slice of that so one slow or
# stuck external API can't blow the whole query's budget — LLD §6 "Partial
# agent timeout" row: a timed-out agent is treated identically to an errored
# one, never left to hang the query.
AGENT_TIMEOUT_SECONDS = 6.0


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
    # Human-readable step log for FR-PLAN-4 / FR-UI-3 (the WebSocket
    # trace_update stream, LLD §5.2, is built by replaying this list).
    trace: Annotated[list[str], operator.add]


def _agent_requested(state: GraphState, agent_name: str) -> bool:
    plan = state.get("plan")
    if plan is None or plan.needs_clarification:
        return False
    return any(inv.agent_name == agent_name for inv in plan.invocations)


async def _call_bounded(fn: Any, *args: Any, unavailable: Any, agent_label: str) -> tuple[Any, str]:
    """Runs a (synchronous, per the LLD's agent method signatures) agent call
    in a worker thread, bounded by AGENT_TIMEOUT_SECONDS. Returns
    (result_or_unavailable, trace_line) — never raises, per LLD §6: a failed
    or timed-out agent must degrade to an 'unavailable' result, not crash the
    query or the whole orchestration graph."""
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(fn, *args), timeout=AGENT_TIMEOUT_SECONDS
        )
        return result, f"{agent_label}: data received"
    except TimeoutError:
        return unavailable, f"{agent_label}: timed out after {AGENT_TIMEOUT_SECONDS}s (unavailable)"
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
        result, trace_line = await _call_bounded(
            agent.get_conditions,
            location,
            None,  # TimeWindow — TODO(P1): resolve from Planner's time_window_text
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
        result, trace_line = await _call_bounded(
            agent.get_nearest_pfz,
            location,
            unavailable=None,
            agent_label="Ocean Agent",
        )
        return {"results": {"ocean": result}, "trace": [trace_line]}

    return _node


def _geofencing_node(agent: GeofencingAgent):
    async def _node(state: GraphState) -> dict[str, Any]:
        if not _agent_requested(state, "geofencing"):
            return {}
        location = _location_for(state, "geofencing")
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
            return {"composed": composed, "trace": ["Synthesis: skipped (clarification requested)"]}

        composed = await asyncio.to_thread(
            agent.compose, plan, state.get("results", {}), state.get("language", "en")
        )
        return {"composed": composed, "trace": ["Synthesis: response composed"]}

    return _node


def _location_for(state: GraphState, agent_name: str) -> Any:
    """TODO(P1): once AgentInvocationRequest payloads are finalized against
    real agent signatures (they currently carry a plain `location` dict, LLD
    §2.2's ExecutionPlan, but WeatherAgent/OceanAgent/GeofencingAgent expect
    a LatLon per their LLD §2.3-2.5 signatures), convert here. Left as a
    single seam so the conversion logic isn't duplicated across three nodes."""
    plan = state.get("plan")
    if plan is None:
        return None
    for inv in plan.invocations:
        if inv.agent_name == agent_name:
            return inv.input_payload.get("location")
    return None


def _unavailable_weather_result() -> Any:
    from app.schemas.weather import WeatherResult

    return WeatherResult(wind_speed_kmh=0.0, wave_height_m=0.0, status="unavailable")


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
