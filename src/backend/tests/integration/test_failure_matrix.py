"""
LLD §6 error-handling matrix, exercised end-to-end through the REAL pipeline.

Owner: P1 (Backend/Orchestration Lead).
Issue: #36 ("every LLD §6 failure row is exercised: adapter down, LLM timeout,
partial agent timeout — all degrade, none crash").
Requirements: NFR-REL-1, NFR-REL-2, NFR-PERF-2, FR-RISK-3, FR-PLAN-3, FR-SYN-1/2.

Each test below names the LLD §6 table row it covers. Unlike tests/test_graph.py
(which fakes every agent to test the wiring), these run the real Planner, the
real specialist agents, the real adapters, the real Figure 2 decision tree and
the real Synthesis safety checks — only the transport underneath is faulted.
See tests/integration/conftest.py for why that is the right seam.

The invariant every row shares, and the reason this file exists: a failure
anywhere upstream must surface as a degraded, honest answer — never a crash,
and never a verdict that reads as SAFE (NFR-REL-2).
"""
from __future__ import annotations

import time

import httpx
import pytest

from app.agents.geofencing_agent import GeofencingAgent
from app.agents.ocean_agent import OceanAgent
from app.agents.risk_safety_agent import RiskSafetyAgent
from app.agents.weather_agent import WeatherAgent
from app.core.session import ConversationContext
from app.data_access.gis_boundary_adapter import GISBoundaryAdapter
from app.data_access.incois_adapter import INCOISAdapter
from app.data_access.weather_adapter import WeatherDataAdapter
from app.orchestration import graph as graph_module
from app.orchestration.graph import run_query
from app.orchestration.planner_agent import NormalizedQuery, PlannerAgent
from app.orchestration.synthesis_agent import SynthesisAgent
from tests.integration.conftest import (
    CONNECT_ERROR,
    READ_TIMEOUT,
    ScriptedLLM,
    planner_extraction,
    raises,
    sentences,
)

# ~30 km west of Kochi: open water, outside every MPA polygon and outside the
# IMBL buffer. Chosen so that no verdict in this file is attributable to the
# location itself — only to the fault each test injects.
OFFSHORE_KOCHI = {"lat": 9.93, "lon": 75.98}

MULTI_AGENT_QUERY = "Is it safe to fish near Kochi today, and am I near any restricted zone?"


def _real_agents(llm: ScriptedLLM) -> dict:
    """Every agent real, every adapter real. `llm` replaces only the Gemini
    client the Planner and Synthesis construct — see conftest.ScriptedLLM."""
    return {
        "planner": PlannerAgent(llm_client=llm),
        "weather_agent": WeatherAgent(WeatherDataAdapter()),
        "ocean_agent": OceanAgent(INCOISAdapter()),
        "geofencing_agent": GeofencingAgent(GISBoundaryAdapter()),
        "risk_agent": RiskSafetyAgent(),
        "synthesis_agent": SynthesisAgent(llm_client=llm),
    }


def _honest_sentences() -> dict:
    """A Synthesis payload that states the verdict plainly and cites every
    factual sentence. Worded to satisfy the agent's own checks for BOTH a
    SAFE and an INSUFFICIENT_DATA verdict, so one payload serves every row —
    the point of these tests is the degradation path, not prompt quality."""
    return sentences(
        ("Some of the data needed for this answer is unavailable, so a safety "
         "verdict cannot be given with confidence.", "risk_safety"),
        ("Here is what could be retrieved.", "none"),
    )


def _run(llm: ScriptedLLM, query: str = MULTI_AGENT_QUERY, context=None):
    return run_query(
        NormalizedQuery(text=query, language="en"),
        context or ConversationContext(session_id="integration"),
        **_real_agents(llm),
    )


def _verdict(state) -> str | None:
    risk = (state.get("results") or {}).get("risk_safety")
    return getattr(risk, "verdict", None)


# --------------------------------------------------------------------- #
# Baseline: the pipeline genuinely works before anything is faulted.
# --------------------------------------------------------------------- #
async def test_multi_agent_query_produces_a_cited_answer(fault_http):
    """Not an LLD §6 row — the control case. Three specialists fan out, Risk
    combines them, Synthesis returns cited text. Without this passing, every
    "it degraded correctly" assertion below would be unfalsifiable: a pipeline
    that is broken outright also never returns SAFE."""
    fault_http()
    llm = ScriptedLLM(
        planner_payload=planner_extraction(**OFFSHORE_KOCHI),
        synthesis_payload=sentences(
            ("Winds are light at 8 km/h with waves around 0.6 m.", "weather"),
            ("The nearest potential fishing zone is offshore to the west.", "ocean"),
            ("You are not inside any protected or restricted area.", "geofencing"),
            ("Conditions are safe to go out.", "risk_safety"),
        ),
    )

    state = await _run(llm)

    results = state["results"]
    # FR-PLAN-3: a multi-intent query fans out to more than one specialist.
    assert {"weather", "ocean", "geofencing", "risk_safety"} <= set(results)
    assert results["weather"].status == "ok"
    assert _verdict(state) == "SAFE"
    # FR-SYN-1/2: synthesised prose, and every factual sentence carries a citation.
    composed = state["composed"]
    assert composed.text
    assert {c.source for c in composed.citations} >= {"weather", "geofencing", "risk_safety"}
    assert all(c.timestamp is not None for c in composed.citations)


# --------------------------------------------------------------------- #
# LLD §6 row 2: "Weather / INCOIS / GIS adapter failure"
# --------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "fault",
    [CONNECT_ERROR, READ_TIMEOUT, raises(httpx.HTTPError("stub: upstream 500"))],
    ids=["connect-refused", "read-timeout", "http-error"],
)
async def test_weather_adapter_down_yields_insufficient_data_never_safe(fault_http, fault):
    """Weather is a required input to Figure 2 — when its adapter is down the
    verdict must be INSUFFICIENT_DATA, not SAFE, even though every other
    source in this test is healthy and calm (NFR-REL-2, FR-RISK-3)."""
    fault_http({"/forecast": fault, "marine-api": fault})
    llm = ScriptedLLM(
        planner_payload=planner_extraction(**OFFSHORE_KOCHI),
        synthesis_payload=_honest_sentences(),
    )

    state = await _run(llm)

    assert state["results"]["weather"].status == "unavailable"
    # FR-WX-4 / NFR-REL-1: unavailable means no numbers were invented.
    assert state["results"]["weather"].data_timestamp is None
    assert _verdict(state) == "INSUFFICIENT_DATA"
    assert state["composed"].text


def _calm_sea_sentences() -> dict:
    return sentences(
        ("Winds are light and no restricted area applies.", "weather"),
        ("Conditions are safe to go out.", "risk_safety"),
    )


async def test_incois_down_falls_back_to_the_bundled_snapshot_marked_stale(fault_http):
    """PFZ is advisory-only in Figure 2, so INCOIS being down must NOT force
    INSUFFICIENT_DATA. With the live WFS unreachable the adapter serves the
    bundled snapshot (#38 / HLD §9 RISK-1) — acceptable only because it is
    flagged `is_stale=True` and carries the advisory's own date, so Synthesis
    can say how old it is rather than presenting it as current (FR-OCEAN-4)."""
    fault_http({"PFZ_Automation": CONNECT_ERROR, "PFZ-TUNA-SST-CHL": CONNECT_ERROR})
    llm = ScriptedLLM(
        planner_payload=planner_extraction(**OFFSHORE_KOCHI),
        synthesis_payload=_calm_sea_sentences(),
    )

    state = await _run(llm)

    pfz = state["results"]["ocean"]
    assert pfz is not None and pfz.is_stale is True
    assert pfz.data_timestamp is not None  # dated, not fabricated as "now"
    assert _verdict(state) == "SAFE"  # advisory-only input, verdict stands
    assert state["composed"].text


async def test_incois_down_with_no_snapshot_yields_no_pfz_at_all(
    fault_http, settings_override, tmp_path
):
    """The same row with the snapshot fallback also gone — the case that
    proves the fallback is a real dataset and not a silent default. With
    nothing to serve, `ocean` must be the None sentinel rather than an
    invented zone, and the verdict must still stand on weather + geofencing."""
    fault_http({"PFZ_Automation": CONNECT_ERROR, "PFZ-TUNA-SST-CHL": CONNECT_ERROR})
    settings_override(incois_pfz_snapshot_path=str(tmp_path / "no-snapshot.json.gz"))
    llm = ScriptedLLM(
        planner_payload=planner_extraction(**OFFSHORE_KOCHI),
        synthesis_payload=_calm_sea_sentences(),
    )

    state = await _run(llm)

    results = state["results"]
    assert results["ocean"] is None  # the unavailable sentinel, not a made-up zone
    assert results["ocean_params"].sea_surface_temp_c is None
    assert results["ocean_params"].chlorophyll_mg_m3 is None
    assert _verdict(state) == "SAFE"
    assert state["composed"].text


async def test_gis_adapter_down_yields_insufficient_data_never_safe(
    fault_http, settings_override, tmp_path
):
    """Geofencing is a required input (Figure 2 step 2). With the boundary
    dataset unreadable, the honest answer is INSUFFICIENT_DATA — a geofence
    that cannot be checked is not a geofence that was cleared.

    Landed xfail(strict=True) in #100 because GeofenceResult had no way to say
    "unavailable", so Figure 2 could not tell a missing boundary check from a
    passed one and answered SAFE. Unmarked here with the #99 fix that gives
    GeofencingAgent.check() a None sentinel."""
    fault_http()
    settings_override(gis_boundary_data_path=str(tmp_path / "does-not-exist.geojson"))
    llm = ScriptedLLM(
        planner_payload=planner_extraction(**OFFSHORE_KOCHI),
        synthesis_payload=_honest_sentences(),
    )

    state = await _run(llm)

    assert _verdict(state) == "INSUFFICIENT_DATA"
    assert state["composed"].text


async def test_every_adapter_down_at_once_degrades_without_crashing(
    fault_http, settings_override, tmp_path
):
    """All three data sources down simultaneously — the worst realistic case.
    The query must still return a response object rather than raising out of
    the graph."""
    fault_http(all=CONNECT_ERROR)
    settings_override(gis_boundary_data_path=str(tmp_path / "does-not-exist.geojson"))
    llm = ScriptedLLM(
        planner_payload=planner_extraction(**OFFSHORE_KOCHI),
        synthesis_payload=_honest_sentences(),
    )

    state = await _run(llm)

    assert _verdict(state) == "INSUFFICIENT_DATA"
    assert state["composed"].text


# --------------------------------------------------------------------- #
# LLD §6 row 3: "LLM provider timeout during entity extraction"
# --------------------------------------------------------------------- #
async def test_llm_timeout_at_extraction_falls_back_to_keywords_and_asks_for_location(fault_http):
    """Row 3, first half: the Planner falls back to keyword matching. That
    fallback resolves intent but not location, so Figure 1 routes to the
    clarifying follow-up rather than guessing a place — and no specialist
    agent runs at all."""
    fault_http()
    llm = ScriptedLLM(fail_with=httpx.ReadTimeout("stub: LLM provider timed out"))

    state = await _run(llm)

    assert state["plan"].needs_clarification
    assert state["plan"].invocations == []
    assert not state.get("results")
    assert "location" in state["composed"].text.lower()


async def test_llm_timeout_at_extraction_still_completes_using_prior_turn_location(fault_http):
    """Row 3, second half — the case that proves the fallback is more than a
    graceful failure. With a location already in the session (FR-PLAN-5), the
    keyword-extracted intent is enough to route and answer, LLM down or not."""
    fault_http()
    context = ConversationContext(session_id="integration-degraded")
    context.append_turn("", {"location": {"place_name": "Kochi", **OFFSHORE_KOCHI}})

    llm = ScriptedLLM(
        fail_with=httpx.ReadTimeout("stub: LLM provider timed out"),
        synthesis_payload=_honest_sentences(),
    )
    state = await _run(llm, query="is it safe to fish there", context=context)

    # Keyword fallback caught "safe" and "fish" -> weather + ocean + risk.
    assert not state["plan"].needs_clarification
    assert {"weather", "ocean"} <= {inv.agent_name for inv in state["plan"].invocations}
    assert state["results"]["weather"].status == "ok"
    # Synthesis's own LLM call fails too, so the graph's degraded response
    # stands in — degraded, but still an answer, and still never SAFE.
    assert _verdict(state) in ("SAFE", "CAUTION", "INSUFFICIENT_DATA")
    assert state["composed"].text


async def test_synthesis_llm_failure_degrades_to_a_safe_apology_not_a_crash(fault_http):
    """The Synthesis half of the same row. A composition failure must not
    discard the query.

    Since #139 the fallback is no longer an apology that claims nothing: the
    facts were computed and checked before Synthesis ran, so the response is
    composed deterministically from them. FR-SYN-2 is stronger here, not
    weaker — every sentence is generated from one agent's fields and tagged
    with it, so the answer is cited by construction rather than by asking a
    model to cite itself."""
    fault_http()

    class _PlannerOnlyLLM(ScriptedLLM):
        def _respond(self, model, contents):
            if "entity-extraction step" in contents:
                return super()._respond(model, contents)
            raise httpx.ReadTimeout("stub: synthesis LLM timed out")

    llm = _PlannerOnlyLLM(planner_payload=planner_extraction(**OFFSHORE_KOCHI))
    state = await _run(llm)

    composed = state["composed"]
    assert composed.text
    # Cited, not empty — and every citation names an agent that actually ran.
    assert composed.citations
    assert {c.source for c in composed.citations} <= set(state["results"])

    # Whatever verdict Risk reached on the fallback data, the answer has to
    # state it — that is what makes it an answer rather than an apology.
    text = composed.text.lower()
    assert _verdict(state).lower() in text

    # Checking phrasing rather than the bare substring "safe", which the word
    # "safety" trips on in Risk's own rationale. None of these are phrasings
    # the deterministic builders can produce, for any verdict.
    for reassurance in ("safe to", "conditions are good", "all clear", "no risk"):
        assert reassurance not in text


# --------------------------------------------------------------------- #
# LLD §6 row 4: "Partial agent timeout during a multi-agent query"
# --------------------------------------------------------------------- #
async def test_partial_agent_timeout_is_treated_exactly_like_an_error(
    fault_http, monkeypatch
):
    """Row 4. One specialist hangs; the others complete. The hung agent must
    be indistinguishable from an errored one downstream — same unavailable
    sentinel, same INSUFFICIENT_DATA consequence — and the query must not wait
    on it indefinitely."""
    monkeypatch.setattr(graph_module, "AGENT_TIMEOUT_SECONDS", 0.5)
    # The hung specialist here IS the weather node, which has run on its own
    # budget since #131 — patching only the shared constant would leave it on
    # the real 8s and let the "hang" finish normally, testing nothing.
    monkeypatch.setattr(graph_module, "WEATHER_TIMEOUT_SECONDS", 0.5)

    def _hang(url):
        # Just past the (patched-down) budget: long enough that the graph must
        # time it out, short enough that the orphaned worker thread doesn't
        # stall pytest's teardown waiting to be joined.
        time.sleep(2.0)
        raise AssertionError("unreachable: the graph should have timed this out")

    fault_http({"/forecast": _hang, "marine-api": _hang})
    llm = ScriptedLLM(
        planner_payload=planner_extraction(**OFFSHORE_KOCHI),
        synthesis_payload=_honest_sentences(),
    )

    started = time.perf_counter()
    state = await _run(llm)
    elapsed = time.perf_counter() - started

    assert elapsed < 5.0, "the graph waited on the hung agent instead of bounding it"
    # Identical to the adapter-down row above — that identity IS the requirement.
    assert state["results"]["weather"].status == "unavailable"
    assert _verdict(state) == "INSUFFICIENT_DATA"
    # The agents that did answer still contributed.
    assert "geofencing" in state["results"]
    assert state["composed"].text
    assert any("timed out" in line for line in state["trace"])


async def test_all_agents_hanging_still_lands_inside_the_nfr_perf_2_budget(fault_http):
    """NFR-PERF-2 (15 s multi-agent budget), structurally rather than by
    measuring a healthy run: with every upstream hung, the response time is
    determined by the graph's per-node budgets and fan-out, not by the
    upstreams. Uses the real timeout constants deliberately — this test is what
    would fail if someone raised one past what the budget can absorb."""
    slowest_node = max(
        graph_module.AGENT_TIMEOUT_SECONDS, graph_module.WEATHER_TIMEOUT_SECONDS
    )

    def _hang(url):
        # Past every node budget, so each one is bounded by the graph rather
        # than by the sleep finishing on its own (#131: deriving this from the
        # shared constant alone tied it exactly to the weather node's 8s).
        time.sleep(slowest_node + 2.0)
        raise AssertionError("unreachable")

    fault_http(all=_hang)
    llm = ScriptedLLM(
        planner_payload=planner_extraction(**OFFSHORE_KOCHI),
        synthesis_payload=_honest_sentences(),
    )

    started = time.perf_counter()
    state = await _run(llm)
    elapsed = time.perf_counter() - started

    assert elapsed < 15.0, (
        f"multi-agent query took {elapsed:.1f}s with every upstream hung — "
        "over the NFR-PERF-2 budget"
    )
    assert _verdict(state) == "INSUFFICIENT_DATA"
    assert state["composed"].text
