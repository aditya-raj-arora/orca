"""
Integration tests for the LangGraph orchestration wiring (orchestration/graph.py).

Owner: P1. Every agent is faked — these tests exercise the GRAPH's fan-out/
fan-in/skip/degrade behavior, not any real agent logic (each agent's own
logic is unit-tested where it's implemented, e.g. tests/test_planner_agent.py
for routing). No LLM key, DB, or external API needed to run this file.
"""

from __future__ import annotations

import pytest

from app.core.session import ConversationContext
from app.orchestration.graph import run_query
from app.orchestration.planner_agent import NormalizedQuery
from app.schemas.geofence import GeofenceResult
from app.schemas.ocean import OceanParams, PFZResult
from app.schemas.risk import RiskVerdict
from app.schemas.synthesis import AgentInvocationRequest, ComposedResponse, ExecutionPlan
from app.schemas.weather import WeatherResult

LOCATION = {"lat": 9.93, "lon": 76.26}


class _ExplodingAgent:
    """Any attribute access returns a function that fails the test if
    called — used to assert an agent is never invoked on a given path."""

    def __getattr__(self, name):
        def _boom(*_args, **_kwargs):
            raise AssertionError(f"{name} should not have been called on this path")

        return _boom


async def test_weather_only_plan_skips_ocean_and_geofencing():
    class FakePlanner:
        def plan(self, query, context):
            return ExecutionPlan(
                invocations=[
                    AgentInvocationRequest(
                        agent_name="weather", input_payload={"location": LOCATION}
                    ),
                    AgentInvocationRequest(agent_name="risk_safety", input_payload={}),
                ],
                trace=["fake planner: weather + risk_safety"],
            )

    class FakeWeather:
        def get_conditions(self, location, window):
            return WeatherResult(wind_speed_kmh=12.0, wave_height_m=1.2, status="ok")

    class FakeRisk:
        def evaluate(self, weather, geofence, ocean):
            assert weather is not None and weather.status == "ok"
            assert geofence is None
            assert ocean is None
            return RiskVerdict(verdict="SAFE", rationale="clear", contributing_factors=[])

    class FakeSynthesis:
        def compose(self, plan, results, language):
            assert set(results.keys()) == {"weather", "risk_safety"}
            return ComposedResponse(text="safe")

    state = await run_query(
        NormalizedQuery(text="is it safe to fish near kochi", language="en"),
        ConversationContext(session_id="t1"),
        planner=FakePlanner(),
        weather_agent=FakeWeather(),
        ocean_agent=_ExplodingAgent(),
        geofencing_agent=_ExplodingAgent(),
        risk_agent=FakeRisk(),
        synthesis_agent=FakeSynthesis(),
    )

    assert set(state["results"].keys()) == {"weather", "risk_safety"}
    assert state["composed"].text == "safe"


async def test_all_three_agents_fan_out_and_fan_in():
    class FakePlanner:
        def plan(self, query, context):
            return ExecutionPlan(
                invocations=[
                    AgentInvocationRequest(
                        agent_name="weather", input_payload={"location": LOCATION}
                    ),
                    AgentInvocationRequest(
                        agent_name="ocean", input_payload={"location": LOCATION}
                    ),
                    AgentInvocationRequest(
                        agent_name="geofencing", input_payload={"location": LOCATION}
                    ),
                    AgentInvocationRequest(agent_name="risk_safety", input_payload={}),
                ],
                trace=[],
            )

    class FakeWeather:
        def get_conditions(self, location, window):
            return WeatherResult(wind_speed_kmh=10.0, wave_height_m=0.8, status="ok")

    class FakeOcean:
        def get_nearest_pfz(self, location):
            return PFZResult(
                centroid=None, distance_km=5.0, bearing_deg=45, data_timestamp=None, is_stale=False
            )

        def get_ocean_parameters(self, location):
            return OceanParams(sea_surface_temp_c=28.5, chlorophyll_mg_m3=0.4)

    class FakeGeofencing:
        def check(self, location):
            return GeofenceResult(within_imbl_buffer=False, imbl_distance_km=20.0, within_mpa=False)

    class FakeRisk:
        def evaluate(self, weather, geofence, ocean):
            assert weather is not None
            assert geofence is not None
            assert ocean is not None
            return RiskVerdict(verdict="SAFE", rationale="all clear", contributing_factors=[])

    class FakeSynthesis:
        def compose(self, plan, results, language):
            return ComposedResponse(text="all clear")

    state = await run_query(
        NormalizedQuery(
            text="is it safe near a boundary and any good fishing zones", language="en"
        ),
        ConversationContext(session_id="t2"),
        planner=FakePlanner(),
        weather_agent=FakeWeather(),
        ocean_agent=FakeOcean(),
        geofencing_agent=FakeGeofencing(),
        risk_agent=FakeRisk(),
        synthesis_agent=FakeSynthesis(),
    )

    assert set(state["results"].keys()) == {
        "weather",
        "ocean",
        "ocean_params",
        "geofencing",
        "risk_safety",
    }


async def test_agent_exception_degrades_to_unavailable_not_crash():
    """Matches the real skeleton's current NotImplementedError state — the
    graph must not crash when an agent raises."""

    class FakePlanner:
        def plan(self, query, context):
            return ExecutionPlan(
                invocations=[
                    AgentInvocationRequest(
                        agent_name="weather", input_payload={"location": LOCATION}
                    ),
                    AgentInvocationRequest(agent_name="risk_safety", input_payload={}),
                ],
                trace=[],
            )

    class RaisingWeather:
        def get_conditions(self, location, window):
            raise NotImplementedError("stub")

    class FakeRisk:
        def evaluate(self, weather, geofence, ocean):
            assert weather is not None and weather.status == "unavailable"
            return RiskVerdict(
                verdict="INSUFFICIENT_DATA",
                rationale="weather unavailable",
                contributing_factors=[],
            )

    class FakeSynthesis:
        def compose(self, plan, results, language):
            assert results["risk_safety"].verdict == "INSUFFICIENT_DATA"
            return ComposedResponse(text="insufficient data")

    state = await run_query(
        NormalizedQuery(text="is it safe", language="en"),
        ConversationContext(session_id="t3"),
        planner=FakePlanner(),
        weather_agent=RaisingWeather(),
        ocean_agent=_ExplodingAgent(),
        geofencing_agent=_ExplodingAgent(),
        risk_agent=FakeRisk(),
        synthesis_agent=FakeSynthesis(),
    )

    assert state["results"]["weather"].status == "unavailable"
    assert state["composed"].text == "insufficient data"


async def test_clarification_path_skips_every_agent():
    class ClarifyingPlanner:
        def plan(self, query, context):
            return ExecutionPlan(
                needs_clarification=True,
                clarification_prompt="Which location are you asking about?",
                trace=["planner: location unresolved"],
            )

    state = await run_query(
        NormalizedQuery(text="is it safe", language="en"),
        ConversationContext(session_id="t4"),
        planner=ClarifyingPlanner(),
        weather_agent=_ExplodingAgent(),
        ocean_agent=_ExplodingAgent(),
        geofencing_agent=_ExplodingAgent(),
        risk_agent=_ExplodingAgent(),
        synthesis_agent=_ExplodingAgent(),
    )

    assert state["results"] == {}
    assert state["composed"].text == "Which location are you asking about?"


async def test_no_intent_query_skips_risk_and_all_specialists():
    class InformationalPlanner:
        def plan(self, query, context):
            return ExecutionPlan(invocations=[], trace=["planner: informational only"])

    class FakeSynthesis:
        def compose(self, plan, results, language):
            assert results == {}
            return ComposedResponse(text="informational answer")

    state = await run_query(
        NormalizedQuery(text="what is a PFZ", language="en"),
        ConversationContext(session_id="t5"),
        planner=InformationalPlanner(),
        weather_agent=_ExplodingAgent(),
        ocean_agent=_ExplodingAgent(),
        geofencing_agent=_ExplodingAgent(),
        risk_agent=_ExplodingAgent(),
        synthesis_agent=FakeSynthesis(),
    )

    assert state["results"] == {}
    assert state["composed"].text == "informational answer"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


def test_unavailable_weather_sentinel_does_not_claim_alerts_were_checked():
    """NFR-REL-2 (#110): the graph's own 'weather unavailable' sentinel must
    set alerts_source_available=False, matching WeatherAgent._unavailable().
    It defaults to True, which would assert both alert feeds were reached and
    found clear while the result carries wind=0.0/wave=0.0 — an empty
    active_alerts list read as 'no alerts' rather than 'unknown'. Currently
    masked because RiskSafetyAgent checks status first; this pins it so a
    reordering there can't quietly turn it into a SAFE-leaning verdict."""
    from app.orchestration.graph import _unavailable_weather_result

    sentinel = _unavailable_weather_result()
    assert sentinel.status == "unavailable"
    assert sentinel.alerts_source_available is False
    assert sentinel.active_alerts == []


def test_synthesis_gets_a_larger_budget_than_the_specialists():
    """#118: compose() makes up to TWO sequential Gemini calls (initial +
    regeneration when a safety check rejects the first), so the 6s sized for a
    specialist's single HTTP call made a tripped safety check into a timeout
    and shipped the degraded "couldn't put together an answer" string."""
    from app.orchestration.graph import AGENT_TIMEOUT_SECONDS, SYNTHESIS_TIMEOUT_SECONDS

    assert SYNTHESIS_TIMEOUT_SECONDS > AGENT_TIMEOUT_SECONDS


async def test_call_bounded_reads_the_global_timeout_at_call_time():
    """Regression guard: `timeout` must not be a default argument bound to
    AGENT_TIMEOUT_SECONDS at import — tests/integration/test_failure_matrix.py
    patches that global down to keep the timeout row fast, and an early binding
    silently disables the patch (caught exactly that way)."""

    from app.orchestration import graph as graph_module

    original = graph_module.AGENT_TIMEOUT_SECONDS
    graph_module.AGENT_TIMEOUT_SECONDS = 0.1
    try:
        def _hang():
            import time

            time.sleep(1.0)
            return "should have timed out"

        result, trace = await graph_module._call_bounded(
            _hang, unavailable="sentinel", agent_label="Test Agent"
        )
    finally:
        graph_module.AGENT_TIMEOUT_SECONDS = original

    assert result == "sentinel"
    assert "timed out" in trace


def test_float_rounding_keeps_raw_precision_out_of_the_prompt():
    """#118: Gemini verbalises the payload numbers directly — a real response
    read "sitting 277.6379475729435 km from the IMBL"."""
    from app.orchestration.synthesis_agent import _round_floats

    assert _round_floats({"imbl_distance_km": 277.6379475729435}) == {"imbl_distance_km": 277.64}
    assert _round_floats([1.23456, {"x": 9.87654}]) == [1.23, {"x": 9.88}]
    # bool is an int subclass, not a quantity — must survive untouched
    assert _round_floats({"within_mpa": False}) == {"within_mpa": False}
    passthrough = {"name": "Kochi", "n": 3, "z": None}
    assert _round_floats(passthrough) == passthrough


async def test_synthesis_node_flags_a_degraded_compose():
    """#121: the node must report whether it actually synthesised, so the
    Gateway can withhold a verdict it cannot explain."""
    from app.orchestration.graph import _synthesis_node
    from app.schemas.synthesis import ExecutionPlan

    class _Exploding:
        def compose(self, *_a, **_k):
            raise RuntimeError("stub: synthesis blew up")

    out = await _synthesis_node(_Exploding())({"plan": ExecutionPlan(trace=[]), "results": {}})
    assert out["synthesis_ok"] is False


async def test_synthesis_node_flags_a_successful_compose():
    from app.orchestration.graph import _synthesis_node
    from app.schemas.synthesis import ComposedResponse, ExecutionPlan

    class _Fine:
        def compose(self, *_a, **_k):
            return ComposedResponse(text="All good.")

    out = await _synthesis_node(_Fine())({"plan": ExecutionPlan(trace=[]), "results": {}})
    assert out["synthesis_ok"] is True
    assert out["composed"].text == "All good."


async def test_clarification_path_counts_as_successful_synthesis():
    """The LLM is skipped deliberately there — nothing failed, so the response
    must not be treated as degraded."""
    from app.orchestration.graph import _synthesis_node
    from app.schemas.synthesis import ExecutionPlan

    plan = ExecutionPlan(trace=[], needs_clarification=True, clarification_prompt="Where?")
    out = await _synthesis_node(object())({"plan": plan, "results": {}})
    assert out["synthesis_ok"] is True
    assert out["composed"].text == "Where?"
