"""
tests/unit/test_synthesis_agent.py

Fixture-driven tests for SynthesisAgent.compose() (Issue #9 / #14) — no real
LLM call, no dependency on P3/P4's agent dataclasses (plain dicts work fine,
see synthesis_agent.py's _serialize_result()).
"""
import json
from datetime import UTC, datetime
from unittest.mock import MagicMock

from app.orchestration.synthesis_agent import SynthesisAgent
from app.schemas.risk import RiskVerdict
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
            "data_timestamp": datetime(2026, 9, 1, 6, 0, tzinfo=UTC),
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
    results = {"weather": {"status": "unavailable", "data_timestamp": datetime.now(UTC)}}
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
            "data_timestamp": datetime(2026, 9, 1, 6, 0, tzinfo=UTC),
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

# --------------------------------------------------------------------------- #
# #39 — an UNSAFE / INSUFFICIENT_DATA verdict must survive composition intact
# (FR-RISK-2, NFR-REL-2). The verdict tree makes these non-negotiable; that is
# worth nothing if the sentence the fisherman hears softens them.
# --------------------------------------------------------------------------- #
def _unsafe_results() -> dict:
    return {
        "weather": {
            "wind_speed_kmh": 8.0,
            "wave_height_m": 0.4,
            "active_alerts": [],
            "data_timestamp": datetime(2026, 9, 1, 6, 0, tzinfo=UTC),
            "status": "ok",
        },
        # A real RiskVerdict, not a dict: graph.py hands Synthesis the
        # dataclass, and _degraded_response() reads .verdict off it via
        # getattr — a dict fixture silently loses the verdict in the
        # fallback path, which is exactly the path these tests assert on.
        "risk_safety": RiskVerdict(
            verdict="UNSAFE",
            rationale="Location is within Marine Protected Area 'Gulf of Mannar'.",
            contributing_factors=["within Marine Protected Area 'Gulf of Mannar'"],
        ),
    }


def _plan() -> ExecutionPlan:
    return ExecutionPlan(trace=["Planner: invoked weather, risk_safety"])


def test_unsafe_verdict_softened_by_llm_is_rejected():
    """The exact failure #39 exists to prevent: calm weather is real, but it
    must never be offered as a reason to discount an UNSAFE verdict."""
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = _fake_llm_response(
        [
            {"text": "Winds are light and the sea is calm.", "source": "weather"},
            {"text": "So conditions are fine and you should be safe to head out.",
             "source": "risk_safety"},
        ]
    )

    response = SynthesisAgent(llm_client=fake_client).compose(
        _plan(), _unsafe_results(), language="en"
    )

    # Regenerated once, failed again, then degraded rather than shipping it.
    assert fake_client.models.generate_content.call_count == 2
    assert "UNSAFE" in response.text
    assert "should be safe to head out" not in response.text


def test_unsafe_verdict_omitted_entirely_is_rejected():
    """Silence is the likelier LLM failure than contradiction — a response
    that just never mentions the verdict is as dangerous as one that
    contradicts it."""
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = _fake_llm_response(
        [
            {"text": "Winds near the location are light.", "source": "weather"},
            {"text": "Wave height is around 0.4 m.", "source": "weather"},
        ]
    )

    response = SynthesisAgent(llm_client=fake_client).compose(
        _plan(), _unsafe_results(), language="en"
    )

    assert fake_client.models.generate_content.call_count == 2
    assert "UNSAFE" in response.text


def test_unsafe_verdict_stated_plainly_is_accepted():
    """The guard must not be so blunt that it rejects a correct response —
    favourable weather facts are still allowed alongside the verdict."""
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = _fake_llm_response(
        [
            {"text": "Winds are light and the sea is calm.", "source": "weather"},
            {"text": "This location is inside the Gulf of Mannar Marine Protected "
                     "Area, so it is UNSAFE to fish here.", "source": "risk_safety"},
        ]
    )

    response = SynthesisAgent(llm_client=fake_client).compose(
        _plan(), _unsafe_results(), language="en"
    )

    assert fake_client.models.generate_content.call_count == 1
    assert "calm" in response.text.lower()
    assert "unsafe" in response.text.lower()


def test_insufficient_data_verdict_softened_is_rejected():
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = _fake_llm_response(
        [
            {"text": "No alerts were reported, so conditions are good.",
             "source": "risk_safety"},
        ]
    )
    results = {
        "risk_safety": RiskVerdict(
            verdict="INSUFFICIENT_DATA",
            rationale="Geofence data is unavailable, so a safety verdict cannot be given.",
            contributing_factors=["geofence data unavailable"],
        ),
    }

    response = SynthesisAgent(llm_client=fake_client).compose(
        _plan(), results, language="en"
    )

    assert fake_client.models.generate_content.call_count == 2
    assert "INSUFFICIENT_DATA" in response.text


def test_safe_and_caution_verdicts_are_not_constrained():
    """The guard applies only to UNSAFE / INSUFFICIENT_DATA — a SAFE verdict
    is allowed to read reassuringly, because it is reassuring."""
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = _fake_llm_response(
        [
            {"text": "Conditions are good and it is safe to head out.",
             "source": "risk_safety"},
        ]
    )
    results = {
        "risk_safety": RiskVerdict(
            verdict="SAFE",
            rationale="No geofence violations or active weather alerts.",
            contributing_factors=[],
        ),
    }

    response = SynthesisAgent(llm_client=fake_client).compose(
        _plan(), results, language="en"
    )

    assert fake_client.models.generate_content.call_count == 1
    assert "safe to head out" in response.text
