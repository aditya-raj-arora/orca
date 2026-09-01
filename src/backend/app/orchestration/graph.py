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
"""
from __future__ import annotations

from app.agents.geofencing_agent import GeofencingAgent
from app.agents.ocean_agent import OceanAgent
from app.agents.risk_safety_agent import RiskSafetyAgent
from app.agents.weather_agent import WeatherAgent
from app.orchestration.planner_agent import PlannerAgent
from app.orchestration.synthesis_agent import SynthesisAgent


def build_orchestration_graph():
    """
    TODO(P1):
      1. Define the LangGraph StateGraph with nodes: planner, weather, ocean,
         geofencing, risk, synthesis. Conditional edges out of `planner` per the
         ExecutionPlan.invocations it returns (not every query needs every
         agent — see LLD §4.1 Figure 1).
      2. Fan the invoked specialist agents out in PARALLEL (async), not
         sequentially — SRS NFR-PERF-2 gives an 8s/15s budget for single-/
         multi-agent queries respectively; sequential calls will blow that.
      3. Apply a bounded per-agent timeout (LLD §6 "Partial agent timeout"
         row) — a timed-out agent must be treated identically to an errored
         one when it reaches the Risk/Safety Agent.
      4. `risk` node only runs after weather/ocean/geofencing have all settled
         (or timed out). `synthesis` runs after `risk`.
      5. Emit a `trace_update` WebSocket message (LLD §5.2) at each node
         transition — this is what LLD §5.2's streamed trace_update messages
         and FR-UI-3's live trace panel are built from. Wire this through
         app/main.py's WebSocket handler.

    Return whatever compiled-graph object app/main.py needs to invoke per query.
    """
    _ = (
        PlannerAgent,
        WeatherAgent,
        OceanAgent,
        GeofencingAgent,
        RiskSafetyAgent,
        SynthesisAgent,
    )  # referenced here so imports aren't flagged unused before the graph is built
    raise NotImplementedError
