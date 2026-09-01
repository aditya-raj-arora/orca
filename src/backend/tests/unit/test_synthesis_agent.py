"""
tests/unit/test_synthesis_agent.py

Fixture-driven tests for SynthesisAgent.compose() (Issue #9 / #14) — no real
LLM call, no dependency on P3/P4's agent dataclasses (plain dicts work fine,
see synthesis_agent.py's _serialize_result()).
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import json

from app.orchestration.synthesis_agent import SynthesisAgent
from app.schemas.synthesis import ExecutionPlan


def _fake_llm_response(sentences: list[dict]) -> MagicMock:
    resp = MagicMock()
    resp.text = json.dumps({"sentences": sentences})
    return resp


def test_compose_happy_path():
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = _fake_llm_response(
        [
            {"text": "Winds near Kochi are moderate.", "source": "weather"},
            {"text": "No active cyclone alerts.", "source": "weather"},
            {"text": "Based on this, conditions are CAUTION.", "source": "risk_safety"},
        ]
    )

    agent = SynthesisAgent(llm_client=fake_client)
    results = {
        "weather": {
            "wind_speed_kmh": 18.0,
            "wave_height_m": 1.2,
            "active_alerts": [],
            "data_timestamp": datetime(2026, 9, 1, 6, 0, tzinfo=timezone.utc),
            "status": "ok",
        },
        "risk_safety": {
            "verdict": "CAUTION",
            "rationale": "Moderate wind, no alerts",
            "contributing_factors": ["wind"],
        },
    }
    plan = ExecutionPlan(trace=["Planner: invoked weather, risk_safety"])

    response = agent.compose(plan, results, language="en")

    assert "moderate" in response.text.lower()
    assert len(response.citations) == 2
    assert {c.source for c in response.citations} == {"weather", "risk_safety"}


def test_compose_degrades_on_bad_source():
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = _fake_llm_response(
        [{"text": "Everything looks fine.", "source": "ocean"}]  # "ocean" not in results
    )

    agent = SynthesisAgent(llm_client=fake_client)
    results = {"weather": {"status": "unavailable", "data_timestamp": datetime.now(timezone.utc)}}
    plan = ExecutionPlan(trace=[])

    response = agent.compose(plan, results, language="en")

    assert response.citations == []
    assert "verified" in response.text.lower() or "caution" in response.text.lower()


def test_compose_filters_none_results():
    """graph.py can pass results={"weather": <obj>, "ocean": None,
    "geofencing": None} when location wasn't resolved for those agents —
    compose() must not describe None as data or cite it."""
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = _fake_llm_response(
        [{"text": "Winds are calm.", "source": "weather"}]
    )

    agent = SynthesisAgent(llm_client=fake_client)
    results = {
        "weather": {
            "wind_speed_kmh": 5.0,
            "wave_height_m": 0.5,
            "active_alerts": [],
            "data_timestamp": datetime(2026, 9, 1, 6, 0, tzinfo=timezone.utc),
            "status": "ok",
        },
        "ocean": None,
        "geofencing": None,
    }
    plan = ExecutionPlan(trace=["Planner: invoked weather, ocean, geofencing"])

    response = agent.compose(plan, results, language="en")

    assert {c.source for c in response.citations} == {"weather"}
    sent_prompt = fake_client.models.generate_content.call_args.kwargs["contents"]
    assert '"ocean": null' not in sent_prompt and '"ocean": None' not in sent_prompt


def test_compose_all_none_results_short_circuits_without_llm_call():
    """If every agent result is None, compose() should degrade immediately
    without even calling the LLM."""
    fake_client = MagicMock()
    agent = SynthesisAgent(llm_client=fake_client)
    plan = ExecutionPlan(trace=[])

    response = agent.compose(plan, {"ocean": None, "geofencing": None}, language="en")

    fake_client.models.generate_content.assert_not_called()
    assert response.citations == []