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


async def test_area_scoped_plan_populates_nearby_results(monkeypatch):
    """issue #174: when the Planner marks ocean/geofencing invocations
    scope="area", the nodes also call the list-nearby methods and surface
    ocean_nearby / geofencing_nearby, without disturbing the single-point
    results that Risk/Safety consumes."""
    from app.schemas.geofence import NearbyMPA, NearbyZones
    from app.schemas.ocean import NearbyPFZ

    class FakePlanner:
        def plan(self, query, context):
            return ExecutionPlan(
                invocations=[
                    AgentInvocationRequest(
                        agent_name="ocean",
                        input_payload={"location": LOCATION, "scope": "area"},
                    ),
                    AgentInvocationRequest(
                        agent_name="geofencing",
                        input_payload={"location": LOCATION, "scope": "area"},
                    ),
                    AgentInvocationRequest(agent_name="risk_safety", input_payload={}),
                ],
                trace=[],
            )

    class FakeOcean:
        def get_nearest_pfz(self, location):
            return PFZResult(
                centroid=None, distance_km=5.0, bearing_deg=45, data_timestamp=None, is_stale=False
            )

        def get_ocean_parameters(self, location):
            return OceanParams(sea_surface_temp_c=28.5, chlorophyll_mg_m3=0.4)

        def list_nearby_pfz(self, location):
            return NearbyPFZ(zones=[], radius_km=300.0, data_timestamp=None, is_stale=False)

    class FakeGeofencing:
        def check(self, location):
            return GeofenceResult(within_imbl_buffer=False, imbl_distance_km=20.0, within_mpa=False)

        def list_nearby(self, location):
            return NearbyZones(
                mpas=[NearbyMPA(name="Gulf of Mannar", distance_km=12.0, contains_point=False)],
                radius_km=150.0,
                data_timestamp=None,
            )

    seen = {}

    class FakeRisk:
        def evaluate(self, weather, geofence, ocean):
            # nearby lists are descriptive only — Risk still gets the point results
            assert geofence is not None and ocean is not None
            return RiskVerdict(verdict="SAFE", rationale="clear", contributing_factors=[])

    class FakeSynthesis:
        def compose(self, plan, results, language):
            seen["keys"] = set(results.keys())
            return ComposedResponse(text="ok")

    state = await run_query(
        NormalizedQuery(text="which fishing zones should be avoided near kochi", language="en"),
        ConversationContext(session_id="t6"),
        planner=FakePlanner(),
        weather_agent=_ExplodingAgent(),
        ocean_agent=FakeOcean(),
        geofencing_agent=FakeGeofencing(),
        risk_agent=FakeRisk(),
        synthesis_agent=FakeSynthesis(),
    )

    assert {"ocean", "ocean_params", "ocean_nearby", "geofencing", "geofencing_nearby"} <= set(
        state["results"].keys()
    )
    assert isinstance(state["results"]["ocean_nearby"], NearbyPFZ)
    assert state["results"]["geofencing_nearby"].mpas[0].name == "Gulf of Mannar"
    assert "geofencing_nearby" in seen["keys"]


async def test_point_scoped_plan_has_no_nearby_results():
    """The default path is untouched — no scope, no list-nearby calls."""

    class FakePlanner:
        def plan(self, query, context):
            return ExecutionPlan(
                invocations=[
                    AgentInvocationRequest(
                        agent_name="geofencing", input_payload={"location": LOCATION}
                    ),
                    AgentInvocationRequest(agent_name="risk_safety", input_payload={}),
                ],
                trace=[],
            )

    class FakeGeofencing:
        def check(self, location):
            return GeofenceResult(within_imbl_buffer=False, imbl_distance_km=20.0, within_mpa=False)

        def list_nearby(self, location):
            raise AssertionError("list_nearby must not be called for a point-scoped plan")

    class FakeRisk:
        def evaluate(self, weather, geofence, ocean):
            return RiskVerdict(verdict="SAFE", rationale="clear", contributing_factors=[])

    class FakeSynthesis:
        def compose(self, plan, results, language):
            return ComposedResponse(text="ok")

    state = await run_query(
        NormalizedQuery(text="am I inside a restricted zone at kochi", language="en"),
        ConversationContext(session_id="t7"),
        planner=FakePlanner(),
        weather_agent=_ExplodingAgent(),
        ocean_agent=_ExplodingAgent(),
        geofencing_agent=FakeGeofencing(),
        risk_agent=FakeRisk(),
        synthesis_agent=FakeSynthesis(),
    )

    assert "geofencing_nearby" not in state["results"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
