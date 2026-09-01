"""
Vertical-slice integration: the REAL WeatherAgent + OceanAgent (with real
adapters, HTTP stubbed) driven through the REAL orchestration graph, with a
hand-built ExecutionPlan standing in for the Planner (no LLM key needed).

Get-ahead on #32: proves weather/ocean integrate end to end and that the
graph's dict-shaped `location` payload (LLD §2.2) is handled before
graph._location_for()'s TODO(P1) conversion lands. Owner: P3.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agents.ocean_agent import OceanAgent
from app.agents.weather_agent import WeatherAgent
from app.core.session import ConversationContext
from app.data_access.caching import PfzCachingAdapter
from app.data_access.incois_adapter import INCOISAdapter
from app.data_access.weather_adapter import WeatherDataAdapter
from app.orchestration.graph import run_query
from app.orchestration.planner_agent import NormalizedQuery
from app.schemas.ocean import PFZResult
from app.schemas.risk import RiskVerdict
from app.schemas.synthesis import AgentInvocationRequest, ComposedResponse, ExecutionPlan
from app.schemas.weather import WeatherResult

SAMPLES = Path(__file__).resolve().parents[3] / "docs" / "samples"
# The graph carries `location` as a plain dict (planner_agent.as_location_dict).
LOCATION = {"place_name": "Chennai", "lat": 13.08, "lon": 80.27}


def _json(rel: str) -> dict:
    return json.loads((SAMPLES / rel).read_text())


def _raise(*_a: object) -> object:
    raise RuntimeError("stubbed upstream failure")


class _FakePlanner:
    """Emits a fixed weather+ocean+risk plan — the shape graph._planner_node
    expects, with a dict `location` like the real Planner produces."""

    def plan(self, query: object, context: object) -> ExecutionPlan:
        return ExecutionPlan(
            invocations=[
                AgentInvocationRequest(agent_name="weather", input_payload={"location": LOCATION}),
                AgentInvocationRequest(agent_name="ocean", input_payload={"location": LOCATION}),
                AgentInvocationRequest(agent_name="risk_safety", input_payload={}),
            ],
            trace=["fake planner: weather + ocean + risk_safety @ Chennai"],
        )


class _CapturingRisk:
    def __init__(self) -> None:
        self.seen: dict = {}

    def evaluate(self, weather: object, geofence: object, ocean: object) -> RiskVerdict:
        self.seen = {"weather": weather, "geofence": geofence, "ocean": ocean}
        return RiskVerdict(verdict="SAFE", rationale="clear", contributing_factors=[])


class _CapturingSynthesis:
    def __init__(self) -> None:
        self.results: dict = {}

    def compose(self, plan: object, results: dict, language: str) -> ComposedResponse:
        self.results = results
        return ComposedResponse(text="ok")


def _weather_agent(
    monkeypatch: pytest.MonkeyPatch, *, forecast: object, marine: object
) -> WeatherAgent:
    ad = WeatherDataAdapter()
    _fn = lambda v: (lambda _la, _lo: _raise()) if v is _raise else (lambda _la, _lo: v)  # noqa: E731
    monkeypatch.setattr(ad, "_fetch_forecast", _fn(forecast))
    monkeypatch.setattr(ad, "_fetch_marine", _fn(marine))
    monkeypatch.setattr(ad, "_fetch_weatherapi_alerts", lambda _la, _lo: None)
    monkeypatch.setattr(ad, "_fetch_gdacs_tc", lambda _la, _lo: [])
    return WeatherAgent(ad)


def _ocean_agent(monkeypatch: pytest.MonkeyPatch, *, pfz_geojson: object) -> OceanAgent:
    inner = INCOISAdapter()
    if pfz_geojson is _raise:
        monkeypatch.setattr(inner, "_get_pfz_geojson", _raise)
    else:
        monkeypatch.setattr(inner, "_get_pfz_geojson", lambda: pfz_geojson)
    return OceanAgent(PfzCachingAdapter(inner))


async def _run(
    weather: WeatherAgent, ocean: OceanAgent
) -> tuple[_CapturingRisk, _CapturingSynthesis, dict]:
    risk, synth = _CapturingRisk(), _CapturingSynthesis()

    class _NoGeo:  # geofencing isn't in the plan; node returns {} before touching this
        def check(self, *_a: object) -> None:  # pragma: no cover
            raise AssertionError("geofencing should not run")

    state = await run_query(
        NormalizedQuery(text="is it safe to fish off chennai", language="en"),
        ConversationContext(session_id="it-1"),
        planner=_FakePlanner(),
        weather_agent=weather,
        ocean_agent=ocean,
        geofencing_agent=_NoGeo(),
        risk_agent=risk,
        synthesis_agent=synth,
    )
    return risk, synth, state


async def test_weather_ocean_slice_flows_live_data_with_dict_location(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    weather = _weather_agent(
        monkeypatch,
        forecast=_json("weather/openmeteo_forecast_chennai.json"),
        marine=_json("weather/openmeteo_marine_chennai.json"),
    )
    ocean = _ocean_agent(monkeypatch, pfz_geojson=_json("incois/pfz_wfs_pfzlines_sample.json"))
    risk, synth, state = await _run(weather, ocean)

    w = state["results"]["weather"]
    o = state["results"]["ocean"]
    assert isinstance(w, WeatherResult) and w.status == "ok"
    assert w.wind_speed_kmh > 0 and w.data_timestamp is not None
    assert isinstance(o, PFZResult)
    assert 5.0 < o.centroid.lat < 25.0 and o.distance_km >= 0.0
    # Risk + Synthesis received the real objects
    assert risk.seen["weather"].status == "ok"
    assert isinstance(risk.seen["ocean"], PFZResult)
    assert set(synth.results) == {"weather", "ocean", "risk_safety"}
    assert state["composed"].text == "ok"


async def test_slice_degrades_when_weather_upstream_down(monkeypatch: pytest.MonkeyPatch) -> None:
    weather = _weather_agent(
        monkeypatch,
        forecast=_raise,
        marine=_json("weather/openmeteo_marine_chennai.json"),
    )
    ocean = _ocean_agent(monkeypatch, pfz_geojson=_json("incois/pfz_wfs_pfzlines_sample.json"))
    risk, synth, state = await _run(weather, ocean)

    assert state["results"]["weather"].status == "unavailable"
    assert state["results"]["weather"].wind_speed_kmh == 0.0  # no fabricated value
    assert isinstance(state["results"]["ocean"], PFZResult)  # ocean unaffected
    assert state["composed"].text == "ok"  # graph still completes


async def test_slice_ocean_none_when_incois_down(monkeypatch: pytest.MonkeyPatch) -> None:
    weather = _weather_agent(
        monkeypatch,
        forecast=_json("weather/openmeteo_forecast_chennai.json"),
        marine=_json("weather/openmeteo_marine_chennai.json"),
    )
    ocean = _ocean_agent(monkeypatch, pfz_geojson=_raise)
    risk, synth, state = await _run(weather, ocean)

    assert state["results"]["ocean"] is None
    assert state["results"]["weather"].status == "ok"
    assert state["composed"].text == "ok"
