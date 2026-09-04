"""
Safety regression suite — issue #39.

Owner: P4 (Geospatial & Risk Engineer).
Requirements: FR-RISK-1..3, FR-GEO-4, NFR-REL-2.
Reference: LLD v1.0 §2.6, §4.2 (Figure 2).

WHY THIS FILE EXISTS, SEPARATE FROM tests/test_risk_safety_agent.py AND
tests/test_graph.py:

  - test_risk_safety_agent.py calls evaluate() directly. It proves the
    decision tree is right, but not that the graph feeds it the right
    inputs — an agent can be perfectly correct and still be handed
    weather=None by a mis-wired node.
  - test_graph.py injects FakeRisk()/_ExplodingAgent() everywhere by
    design ("Every agent is faked", its docstring) — it proves the wiring,
    but never runs the real verdict tree.

Nothing ran the REAL RiskSafetyAgent through the REAL graph until this
file, which is the gap #39 names: "Tests run in CI against the wired
graph, not only the isolated agent."

So here the Risk/Safety and Synthesis agents are genuine. Only the
planner and the three data-fetching agents are faked, because they need
network/DB — but they return real LLD dataclasses, so what the verdict
tree sees is exactly what it sees in production. The Synthesis LLM call
is stubbed (no key in CI); everything downstream of it — the citation,
alerts and verdict-phrasing guards — is the real code.

These tests are adversarial on purpose: each one hands the pipeline the
most favourable-looking data it can and asserts the safety verdict
survives anyway.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock

from app.agents.risk_safety_agent import RiskSafetyAgent
from app.core.session import ConversationContext
from app.orchestration.graph import run_query
from app.orchestration.planner_agent import NormalizedQuery
from app.orchestration.synthesis_agent import SynthesisAgent
from app.schemas.geofence import GeofenceResult
from app.schemas.synthesis import AgentInvocationRequest, ComposedResponse, ExecutionPlan
from app.schemas.weather import WeatherResult

LOCATION = {"lat": 9.93, "lon": 76.26}
_NOW = datetime(2026, 9, 3, tzinfo=UTC)


class _ExplodingAgent:
    def __getattr__(self, name):
        def _boom(*_args, **_kwargs):
            raise AssertionError(f"{name} should not have been called on this path")

        return _boom


class _FakePlanner:
    """Routes to weather + geofencing + risk_safety. Ocean is left out —
    it is advisory-only (LLD §4.2) and irrelevant to these invariants."""

    def plan(self, query, context):
        return ExecutionPlan(
            invocations=[
                AgentInvocationRequest(agent_name="weather", input_payload={"location": LOCATION}),
                AgentInvocationRequest(
                    agent_name="geofencing", input_payload={"location": LOCATION}
                ),
                AgentInvocationRequest(agent_name="risk_safety", input_payload={}),
            ],
            trace=["fake planner: weather + geofencing + risk_safety"],
        )


def _calm_weather(**overrides) -> WeatherResult:
    """Deliberately benign: light wind, low waves, no alerts. If any of the
    invariants below can be broken by favourable weather, this is the
    weather that would do it."""
    defaults = dict(
        wind_speed_kmh=8.0,
        wave_height_m=0.4,
        active_alerts=[],
        data_timestamp=_NOW,
        status="ok",
        alerts_source_available=True,
    )
    defaults.update(overrides)
    return WeatherResult(**defaults)


class _FakeWeather:
    def __init__(self, result: WeatherResult) -> None:
        self._result = result

    def get_conditions(self, location, window):
        return self._result


class _FakeGeofencing:
    def __init__(self, result: GeofenceResult | None) -> None:
        self._result = result

    def check(self, location):
        if self._result is None:
            raise RuntimeError("geofencing adapter down")
        return self._result


class _CapturingSynthesis:
    """Records what the graph handed Synthesis, so a test can assert on the
    verdict object the composition step actually received."""

    def __init__(self) -> None:
        self.results: dict = {}

    def compose(self, plan, results, language):
        self.results = dict(results)
        return ComposedResponse(text="captured")


async def _run(weather, geofence, synthesis) -> dict:
    return await run_query(
        NormalizedQuery(text="is it safe to fish here", language="en"),
        ConversationContext(session_id="safety-regression"),
        planner=_FakePlanner(),
        weather_agent=_FakeWeather(weather),
        ocean_agent=_ExplodingAgent(),
        geofencing_agent=_FakeGeofencing(geofence),
        # The real thing — that is the entire point of this file.
        risk_agent=RiskSafetyAgent(),
        synthesis_agent=synthesis,
    )


# --------------------------------------------------------------------------- #
# FR-GEO-4 — a geofence violation cannot be downgraded, through the real graph
# --------------------------------------------------------------------------- #
async def test_mpa_violation_survives_the_graph_with_perfect_weather():
    synthesis = _CapturingSynthesis()
    state = await _run(
        _calm_weather(),
        GeofenceResult(
            within_imbl_buffer=False,
            imbl_distance_km=80.0,
            within_mpa=True,
            mpa_name="Gulf of Mannar",
        ),
        synthesis,
    )

    verdict = state["results"]["risk_safety"]
    assert verdict.verdict == "UNSAFE"
    # ...and the same object reached Synthesis undisturbed.
    assert synthesis.results["risk_safety"].verdict == "UNSAFE"


async def test_imbl_buffer_violation_survives_the_graph_with_perfect_weather():
    synthesis = _CapturingSynthesis()
    state = await _run(
        _calm_weather(),
        GeofenceResult(
            within_imbl_buffer=True,
            imbl_distance_km=2.1,
            within_mpa=False,
            mpa_name=None,
        ),
        synthesis,
    )

    assert state["results"]["risk_safety"].verdict == "UNSAFE"


# --------------------------------------------------------------------------- #
# NFR-REL-2 — missing safety-relevant data never yields SAFE, through the graph
# --------------------------------------------------------------------------- #
async def test_geofencing_failure_yields_insufficient_data_not_safe():
    """The geofencing agent raising is the realistic form of "missing": the
    graph's _call_bounded turns it into results["geofencing"] = None, and
    the verdict tree must refuse to call that SAFE."""
    synthesis = _CapturingSynthesis()
    state = await _run(_calm_weather(), None, synthesis)

    verdict = state["results"]["risk_safety"]
    assert verdict.verdict == "INSUFFICIENT_DATA"
    assert verdict.verdict != "SAFE"


async def test_unavailable_weather_yields_insufficient_data_not_safe():
    synthesis = _CapturingSynthesis()
    state = await _run(
        _calm_weather(status="unavailable"),
        GeofenceResult(
            within_imbl_buffer=False, imbl_distance_km=80.0, within_mpa=False, mpa_name=None
        ),
        synthesis,
    )

    assert state["results"]["risk_safety"].verdict == "INSUFFICIENT_DATA"


async def test_downed_alert_sources_yield_insufficient_data_not_safe():
    """#37's invariant, re-checked through the graph: a WeatherResult can
    look entirely healthy while both alert sources are down, and an empty
    active_alerts list then means "unknown", not "clear"."""
    synthesis = _CapturingSynthesis()
    state = await _run(
        _calm_weather(alerts_source_available=False),
        GeofenceResult(
            within_imbl_buffer=False, imbl_distance_km=80.0, within_mpa=False, mpa_name=None
        ),
        synthesis,
    )

    assert state["results"]["risk_safety"].verdict == "INSUFFICIENT_DATA"


async def test_genuinely_clear_conditions_still_reach_safe():
    """The counterweight: if none of the above can ever be SAFE, this suite
    would also pass against an agent that only ever says UNSAFE. It must
    still be possible to get a SAFE verdict through the real graph."""
    synthesis = _CapturingSynthesis()
    state = await _run(
        _calm_weather(),
        GeofenceResult(
            within_imbl_buffer=False, imbl_distance_km=80.0, within_mpa=False, mpa_name=None
        ),
        synthesis,
    )

    assert state["results"]["risk_safety"].verdict == "SAFE"


# --------------------------------------------------------------------------- #
# FR-RISK-2 — the verdict survives composition too (real SynthesisAgent)
# --------------------------------------------------------------------------- #
def _stub_llm(sentences: list[dict]) -> MagicMock:
    client = MagicMock()
    response = MagicMock()
    response.text = json.dumps({"sentences": sentences})
    client.models.generate_content.return_value = response
    return client


async def test_unsafe_verdict_is_not_softened_by_the_real_synthesis_agent():
    """End to end: real verdict tree -> real Synthesis. The stubbed LLM
    tries to talk the reader into going out anyway; the composed response
    must not ship that."""
    llm = _stub_llm(
        [
            {"text": "Winds are light and the sea is calm.", "source": "weather"},
            {
                "text": "Conditions are fine, so you should be safe to head out.",
                "source": "risk_safety",
            },
        ]
    )
    state = await _run(
        _calm_weather(),
        GeofenceResult(
            within_imbl_buffer=False,
            imbl_distance_km=80.0,
            within_mpa=True,
            mpa_name="Gulf of Mannar",
        ),
        SynthesisAgent(llm_client=llm),
    )

    assert state["results"]["risk_safety"].verdict == "UNSAFE"
    text = state["composed"].text
    assert "safe to head out" not in text
    assert "UNSAFE" in text
