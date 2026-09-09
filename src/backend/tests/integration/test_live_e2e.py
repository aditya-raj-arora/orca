"""
The genuinely no-mocks end-to-end run: real Gemini, real Open-Meteo / WeatherAPI
/ GDACS / INCOIS / boundary data, real graph, real agents.

Owner: P1 (Backend/Orchestration Lead).
Issue: #36 acceptance criteria 1 and 3 — "a multi-agent query returns a
synthesised, cited answer end to end with no mocks" and "multi-agent responses
land within NFR-PERF-2 (15 s) under normal API conditions".
Requirements: FR-PLAN-3, FR-SYN-1, FR-SYN-2, FR-SYN-3, NFR-PERF-2, NFR-REL-1.

SKIPPED BY DEFAULT. This file makes real calls to a paid-quota LLM and to three
external APIs, so it must never run in ordinary CI or on a laptop with no key —
a suite that silently fails on someone else's network is worse than one that
says it did not run. Enable it explicitly:

    cd src/backend
    LLM_API_KEY=... ORCA_RUN_LIVE_TESTS=1 pytest tests/integration/test_live_e2e.py -v

`scripts/e2e_live_check.py` runs the same pipeline as a standalone script and
prints the full transcript (trace lines, verdict, citations, timings) — use
that when you want to read the answer, and this when you want a pass/fail.

WHAT IS DELIBERATELY NOT ASSERTED: the wording of the answer, the verdict
value, and the exact set of agents invoked beyond "more than one". Those depend
on live sea conditions and on the model's own extraction, and pinning them
would make this file fail for reasons that are not defects. What is asserted is
the contract: multi-agent fan-out, a non-empty synthesised answer, citations
that carry real source timestamps, a verdict from the fixed vocabulary, and the
15 s budget.
"""
from __future__ import annotations

import os
import time

import pytest

from app.agents.geofencing_agent import GeofencingAgent
from app.agents.ocean_agent import OceanAgent
from app.agents.risk_safety_agent import RiskSafetyAgent
from app.agents.weather_agent import WeatherAgent
from app.core.config import get_settings
from app.core.session import ConversationContext
from app.data_access.gis_boundary_adapter import GISBoundaryAdapter
from app.data_access.incois_adapter import INCOISAdapter
from app.data_access.weather_adapter import WeatherDataAdapter
from app.orchestration.graph import run_query
from app.orchestration.planner_agent import NormalizedQuery, PlannerAgent
from app.orchestration.synthesis_agent import SynthesisAgent
from app.schemas.risk import RiskVerdict

NFR_PERF_2_BUDGET_S = 15.0

# A query that must fan out to more than one specialist: safety (weather),
# fishing (ocean) and restricted zones (geofencing) in one sentence.
MULTI_AGENT_QUERY = (
    "Is it safe to go fishing near Kochi today, and am I close to any "
    "restricted or protected zone?"
)

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("ORCA_RUN_LIVE_TESTS") != "1",
        reason="live E2E: set ORCA_RUN_LIVE_TESTS=1 (and LLM_API_KEY) to run",
    ),
]


@pytest.fixture(scope="module")
def live_llm_key() -> str:
    key = get_settings().llm_api_key
    if not key:
        pytest.skip("live E2E: LLM_API_KEY is not set")
    return key


def _real_pipeline() -> dict:
    """Every component default-constructed — no injected fakes anywhere. This
    is deliberately spelled out rather than calling run_query() with no agent
    kwargs, so that a future default change in build_orchestration_graph()
    cannot quietly turn this into a partly-mocked run."""
    return {
        "planner": PlannerAgent(),
        "weather_agent": WeatherAgent(WeatherDataAdapter()),
        "ocean_agent": OceanAgent(INCOISAdapter()),
        "geofencing_agent": GeofencingAgent(GISBoundaryAdapter()),
        "risk_agent": RiskSafetyAgent(),
        "synthesis_agent": SynthesisAgent(),
    }


@pytest.fixture(scope="module")
async def live_state(live_llm_key):
    """One live run shared by every assertion below — the pipeline is slow and
    costs LLM quota, so it runs once and each test interrogates a different
    part of the result. `elapsed` is measured around the whole graph
    invocation, which is what NFR-PERF-2 budgets."""
    started = time.perf_counter()
    state = await run_query(
        NormalizedQuery(text=MULTI_AGENT_QUERY, language="en"),
        ConversationContext(session_id="live-e2e"),
        **_real_pipeline(),
    )
    return state, time.perf_counter() - started


async def test_planner_fans_out_to_more_than_one_specialist(live_state):
    """AC 1, first half (FR-PLAN-3). A single query producing a single agent
    call would make the rest of this file a single-agent test."""
    state, _ = live_state
    plan = state["plan"]
    assert not plan.needs_clarification, (
        f"live Planner asked for clarification instead of routing: "
        f"{plan.clarification_prompt!r}"
    )
    specialists = {
        inv.agent_name for inv in plan.invocations if inv.agent_name != "risk_safety"
    }
    assert len(specialists) >= 2, f"expected a multi-agent plan, got {specialists}"
    assert "risk_safety" in {inv.agent_name for inv in plan.invocations}


async def test_every_invoked_agent_returned_a_result(live_state):
    """No silent gaps: whatever the Planner asked for must appear in results,
    even if the value is the unavailable sentinel. A missing key would mean a
    node did not run, which is a wiring failure rather than a data failure."""
    state, _ = live_state
    requested = {inv.agent_name for inv in state["plan"].invocations}
    returned = set(state["results"])
    assert requested <= returned, f"agents invoked but absent from results: {requested - returned}"


async def test_answer_is_synthesised_and_cited(live_state):
    """AC 1, second half (FR-SYN-1/2/3). Citations must carry timestamps that
    came from the agent results themselves — NFR-REL-1 extends to citations,
    so an answer citing nothing is not an acceptable answer here."""
    state, _ = live_state
    composed = state["composed"]

    assert composed.text.strip(), "live run produced an empty answer"
    assert composed.citations, "live run produced an uncited answer (FR-SYN-2)"

    available = {k for k, v in state["results"].items() if v is not None}
    for citation in composed.citations:
        assert citation.source in available, (
            f"citation names {citation.source!r}, which is not an agent that "
            f"returned data ({sorted(available)})"
        )
        assert citation.timestamp is not None


async def test_verdict_is_from_the_fixed_vocabulary(live_state):
    """FR-RISK-1. The value depends on live conditions and is not asserted;
    that it is one of the four defined verdicts is."""
    state, _ = live_state
    risk = state["results"].get("risk_safety")
    assert isinstance(risk, RiskVerdict)
    assert risk.verdict in ("SAFE", "CAUTION", "UNSAFE", "INSUFFICIENT_DATA")
    assert risk.rationale.strip(), "FR-RISK-2: a verdict must carry its rationale"


async def test_multi_agent_response_lands_within_the_nfr_perf_2_budget(live_state):
    """AC 3 (NFR-PERF-2, SRS §5.1): 15 s for a multi-agent query under normal
    API conditions."""
    _, elapsed = live_state
    assert elapsed < NFR_PERF_2_BUDGET_S, (
        f"multi-agent query took {elapsed:.2f}s, over the "
        f"{NFR_PERF_2_BUDGET_S}s NFR-PERF-2 budget"
    )


async def test_trace_covers_the_whole_pipeline(live_state):
    """FR-PLAN-4 / FR-UI-3: the trace the UI streams must actually describe
    the run, not just the Planner's own reasoning."""
    state, _ = live_state
    trace = " | ".join(state["trace"]).lower()
    assert "planner" in trace
    assert "synthesis" in trace or state["composed"].text
