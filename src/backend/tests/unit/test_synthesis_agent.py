"""
tests/unit/test_synthesis_agent.py

Fixture-driven tests for SynthesisAgent.compose() (Issue #9 / #14) — no real
LLM call, no dependency on P3/P4's agent dataclasses (plain dicts work fine,
see synthesis_agent.py's _serialize_result()).
"""
import json
import time
from datetime import UTC, datetime
from unittest.mock import MagicMock

from app.orchestration.synthesis_agent import (
    _BUDGET_RESERVE_S,
    _MIN_API_DEADLINE_S,
    _MIN_CALL_BUDGET_S,
    _THINKING_LEVEL,
    SynthesisAgent,
)
from app.schemas.common import LatLon
from app.schemas.geofence import GeofenceResult
from app.schemas.ocean import PFZResult
from app.schemas.risk import RiskVerdict
from app.schemas.synthesis import AgentInvocationRequest, ExecutionPlan
from app.schemas.weather import WeatherResult


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

    # The uncited sentence is rejected twice, and since #139 what replaces it is
    # a deterministic statement of what we actually know — cited, not an apology.
    assert "Everything looks fine" not in response.text
    assert "unavailable" in response.text.lower()
    assert {c.source for c in response.citations} == {"weather"}


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


def test_compose_enumerates_and_cites_nearby_zone_lists():
    """issue #174: ocean_nearby / geofencing_nearby carry a LIST of zones;
    compose() must serialize the list into the prompt and cite it like any
    other agent."""
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = _fake_llm_response(
        [
            {"text": "Two restricted zones lie within 150 km.", "source": "geofencing_nearby"},
            {"text": "Gulf of Mannar is 12 km to the south.", "source": "geofencing_nearby"},
        ]
    )
    agent = SynthesisAgent(llm_client=fake_client)
    results = {
        "geofencing_nearby": {
            "mpas": [
                {"name": "Gulf of Mannar", "distance_km": 12.0, "contains_point": False},
                {"name": "Pichavaram Mangrove", "distance_km": 88.0, "contains_point": False},
            ],
            "radius_km": 150.0,
            "data_timestamp": datetime(2026, 9, 1, tzinfo=UTC),
        },
    }
    response = agent.compose(ExecutionPlan(trace=[]), results, language="en")

    assert [c.source for c in response.citations] == ["geofencing_nearby"]
    sent_prompt = fake_client.models.generate_content.call_args.kwargs["contents"]
    assert "Gulf of Mannar" in sent_prompt and "Pichavaram Mangrove" in sent_prompt


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


def _plan_with_location(
    lat: float = 9.9312, lon: float = 76.2673, place_name: str = "Kochi"
) -> ExecutionPlan:
    """Same as _plan(), but with a resolved location in invocations — the
    shape _queried_location() (synthesis_agent.py) reads, matching how
    planner_agent.route_query() actually populates input_payload."""
    return ExecutionPlan(
        invocations=[
            AgentInvocationRequest(
                agent_name="weather",
                input_payload={"location": {"place_name": place_name, "lat": lat, "lon": lon}},
            ),
        ],
        trace=["Planner: invoked weather, risk_safety"],
    )

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


# --------------------------------------------------------------------------- #
# #133 — compose() spends its own budget and always returns a real response
#
# Before this, nothing bounded a single generate_content call: the client was
# built with no timeout, so a stalled call ran until graph.py's node timeout
# killed compose() outright. That kill discards the degraded response below —
# which still carries the Risk verdict — in favour of the graph's sentinel,
# which carries nothing. On the deployed backend it threw away a real CAUTION.
# --------------------------------------------------------------------------- #


def _caution_results() -> dict:
    return {
        "risk_safety": RiskVerdict(
            verdict="CAUTION",
            rationale="Moderate wind, no alerts",
            contributing_factors=["wind"],
        ),
    }


def test_generation_is_bounded_by_the_remaining_budget():
    """The per-call timeout is what makes the budget real — without it the SDK
    default applies and a stalled call outlives compose() entirely."""
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = _fake_llm_response(
        [{"text": "Conditions are CAUTION.", "source": "risk_safety"}]
    )

    SynthesisAgent(llm_client=fake_client, budget_s=20.0).compose(
        _plan(), _caution_results(), language="en"
    )

    config = fake_client.models.generate_content.call_args.kwargs["config"]
    timeout_ms = config["http_options"]["timeout"]
    assert 0 < timeout_ms <= 20.0 * 1000  # milliseconds, per HttpOptions.timeout


def test_deadline_is_never_sent_below_the_api_minimum():
    """#135: HttpOptions.timeout is a SERVER-side deadline and Gemini 400s
    anything under 10s ("Manually set deadline 5s is too short"). #134 shipped
    8.5s here and every call failed instantly in production — invisible to the
    suite because every test mocks the client, so the API never validated it.

    Small budgets must clamp UP to the minimum: the deadline is a ceiling, not
    a reservation, and a call that answers in 2s still answers in 2s."""
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = _fake_llm_response(
        [{"text": "Conditions are CAUTION.", "source": "risk_safety"}]
    )

    SynthesisAgent(llm_client=fake_client, budget_s=_MIN_CALL_BUDGET_S + 1.0).compose(
        _plan(), _caution_results(), language="en"
    )

    config = fake_client.models.generate_content.call_args.kwargs["config"]
    assert config["http_options"]["timeout"] >= _MIN_API_DEADLINE_S * 1000


def test_planner_deadline_is_never_sent_below_the_api_minimum():
    """Same 400, other call site — this is the one that actually broke the
    deployed query, sending well-formed input down the keyword fallback."""
    from app.orchestration.planner_agent import _LLM_TIMEOUT_S

    assert _LLM_TIMEOUT_S >= _MIN_API_DEADLINE_S


def test_no_budget_left_returns_the_verdict_instead_of_calling_the_llm():
    """With too little budget for a round trip, compose() must not start one —
    it returns the degraded response, which still names the verdict."""
    fake_client = MagicMock()

    response = SynthesisAgent(llm_client=fake_client, budget_s=0.0).compose(
        _plan(), _caution_results(), language="en"
    )

    fake_client.models.generate_content.assert_not_called()
    assert "CAUTION" in response.text


def test_a_failing_llm_call_still_returns_the_verdict():
    """An exception used to propagate to _call_bounded and become the graph's
    sentinel — losing the verdict as well as the explanation. Since #139 the
    verdict comes back inside a composed, cited answer."""
    fake_client = MagicMock()
    fake_client.models.generate_content.side_effect = RuntimeError("gemini exploded")

    response = SynthesisAgent(llm_client=fake_client).compose(
        _plan(), _caution_results(), language="en"
    )

    assert "CAUTION" in response.text
    assert {c.source for c in response.citations} == {"risk_safety"}


def test_regeneration_is_skipped_when_the_budget_cannot_fit_it():
    """The first attempt tripped a safety check, so it must not ship (FR-SYN-2).
    With no budget for a second call the answer is the degraded response — not
    a second call that gets cut off, and not the unsafe sentences."""
    fake_client = MagicMock()

    def _slow_and_uncited(*_args, **_kwargs):
        # The call has to actually spend budget — a MagicMock returning
        # instantly leaves the deadline untouched and both calls fit.
        time.sleep(0.3)
        # Uncited source -> _citation_coverage_ok() rejects it.
        return _fake_llm_response([{"text": "Everything looks fine.", "source": "ocean"}])

    fake_client.models.generate_content.side_effect = _slow_and_uncited

    # Enough budget for the first call, not for a second once it has spent 0.3s.
    agent = SynthesisAgent(
        llm_client=fake_client, budget_s=_MIN_CALL_BUDGET_S + _BUDGET_RESERVE_S + 0.2
    )
    response = agent.compose(_plan(), _caution_results(), language="en")

    assert fake_client.models.generate_content.call_count == 1
    assert "Everything looks fine" not in response.text
    assert "CAUTION" in response.text


def test_degraded_response_never_names_a_safe_verdict(monkeypatch):
    """#133/NFR-REL-2: 'I have a SAFE assessment...' on a response that verified
    nothing is the reassurance this system exists not to give.

    Since #139 the apology is the LAST resort, not the first — reached only
    when there is nothing to compose from either. Driven here by making
    deterministic composition come up empty, so the guard stays tested on the
    path that still reaches it."""
    import app.orchestration.synthesis_agent as sa

    monkeypatch.setattr(sa, "_deterministic_sentences", lambda _results: [])
    fake_client = MagicMock()
    fake_client.models.generate_content.side_effect = RuntimeError("gemini exploded")
    results = {
        "risk_safety": RiskVerdict(
            verdict="SAFE", rationale="No violations or alerts", contributing_factors=[]
        ),
    }

    response = SynthesisAgent(llm_client=fake_client).compose(
        _plan(), results, language="en"
    )

    assert "safe" not in response.text.lower()
    assert response.verified is False


def test_degraded_responses_are_marked_unverified(monkeypatch):
    """The Gateway withholds the verdict badge on these (#121). It used to tell
    them apart by object identity against the graph's own sentinel, which this
    one — built inside SynthesisAgent — sailed straight past."""
    import app.orchestration.synthesis_agent as sa

    monkeypatch.setattr(sa, "_deterministic_sentences", lambda _results: [])
    fake_client = MagicMock()
    fake_client.models.generate_content.side_effect = RuntimeError("gemini exploded")

    response = SynthesisAgent(llm_client=fake_client).compose(
        _plan(), _caution_results(), language="en"
    )

    assert response.verified is False


def test_thinking_level_is_capped_on_every_generation():
    """#137: unconstrained, this model spends 1185 thought tokens to write 260
    and the median call lands ON the 10s deadline the API enforces — the
    deployed query 504'd about as often as not. LOW measured a 3.4s median.

    Pinned as the enum, not a budget: this model rejects thinking_budget=0 with
    a 400 despite the SDK documenting "0 is DISABLED"."""
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = _fake_llm_response(
        [{"text": "Conditions are CAUTION.", "source": "risk_safety"}]
    )

    SynthesisAgent(llm_client=fake_client).compose(
        _plan(), _caution_results(), language="en"
    )

    config = fake_client.models.generate_content.call_args.kwargs["config"]
    assert config["thinking_config"] == {"thinking_level": _THINKING_LEVEL}


def test_thinking_config_is_a_shape_the_sdk_accepts():
    """The dict above is coerced by the SDK, not validated by us — so validate
    it here. Two production breakages this session (a 5s deadline, and
    thinking_budget=0) were both shapes the API rejected that no test saw,
    because every other test mocks the client away."""
    from google.genai import types

    coerced = types.GenerateContentConfig(
        **{
            "response_mime_type": "application/json",
            "thinking_config": {"thinking_level": _THINKING_LEVEL},
        }
    )

    assert coerced.thinking_config.thinking_level == _THINKING_LEVEL


# --------------------------------------------------------------------------- #
# #139 — a real answer without the LLM.
#
# The model's job was only ever phrasing: every fact is computed and checked
# before Synthesis is called. So a slow or failing Gemini call is no reason to
# tell a fisherman we have nothing.
# --------------------------------------------------------------------------- #


def _dead_client() -> MagicMock:
    client = MagicMock()
    client.models.generate_content.side_effect = RuntimeError("gemini unavailable")
    return client


def _full_results(**overrides) -> dict:
    results = {
        "weather": WeatherResult(
            wind_speed_kmh=22.3,
            wave_height_m=1.4,
            active_alerts=[],
            data_timestamp=datetime(2026, 9, 6, 17, 21, tzinfo=UTC),
            status="ok",
        ),
        "geofencing": GeofenceResult(
            within_imbl_buffer=False,
            imbl_distance_km=214.8,
            within_mpa=False,
        ),
        "risk_safety": RiskVerdict(
            verdict="CAUTION",
            rationale="No active weather alerts, but conditions are marginal: wind 22 km/h.",
            contributing_factors=["wind 22 km/h"],
        ),
    }
    results.update(overrides)
    return results


def test_without_the_llm_the_answer_is_real_and_verified():
    """The headline: a dead LLM now yields a factual, cited answer carrying the
    verdict — not 'I couldn't generate a fully verified explanation'."""
    response = SynthesisAgent(llm_client=_dead_client()).compose(
        _plan(), _full_results(), language="en"
    )

    assert response.verified is True  # so the Gateway renders the verdict
    assert "couldn't" not in response.text.lower()
    assert "22 km/h" in response.text  # the real wind reading
    assert "CAUTION" in response.text
    assert {c.source for c in response.citations} == {
        "weather",
        "geofencing",
        "risk_safety",
    }


def test_without_the_llm_every_sentence_still_carries_a_citation():
    """FR-SYN-2 holds by construction here — each sentence is generated from
    one agent's fields — but assert it rather than assuming it."""
    from app.orchestration.synthesis_agent import _deterministic_sentences

    results = _full_results()
    sentences = _deterministic_sentences(results)

    assert sentences
    assert all(s["source"] in results for s in sentences)


def test_without_the_llm_an_unsafe_verdict_is_stated_plainly():
    """The deterministic path must clear the same #39 guard the LLM output
    does: state the verdict, never soften it."""
    results = _full_results(
        risk_safety=RiskVerdict(
            verdict="UNSAFE",
            rationale="Location is within Marine Protected Area 'Gulf of Mannar'.",
            contributing_factors=["within Marine Protected Area 'Gulf of Mannar'"],
        ),
        geofencing=GeofenceResult(
            within_imbl_buffer=False,
            imbl_distance_km=180.0,
            within_mpa=True,
            mpa_name="'Gulf of Mannar'",
        ),
    )

    response = SynthesisAgent(llm_client=_dead_client()).compose(
        _plan(), results, language="en"
    )

    assert response.verified is True
    assert "UNSAFE" in response.text
    assert "Gulf of Mannar" in response.text


def test_without_the_llm_unknown_alerts_are_never_phrased_as_clear():
    """NFR-REL-2, the trap this system is most careful about: with both alert
    sources down, active_alerts == [] means UNKNOWN, not 'no alerts'. The
    deterministic wording has to satisfy the same phrasing check the LLM's
    does — if it didn't, compose() would reject its own output and apologise."""
    results = _full_results(
        weather=WeatherResult(
            wind_speed_kmh=8.0,
            wave_height_m=0.4,
            active_alerts=[],
            alerts_source_available=False,
            data_timestamp=datetime(2026, 9, 6, 17, 21, tzinfo=UTC),
            status="ok",
        ),
        risk_safety=RiskVerdict(
            verdict="INSUFFICIENT_DATA",
            rationale=(
                "Both severe-weather alert sources are currently unreachable, so "
                "whether any alerts are active cannot be confirmed."
            ),
            contributing_factors=["weather alert sources unavailable"],
        ),
    )

    response = SynthesisAgent(llm_client=_dead_client()).compose(
        _plan(), results, language="en"
    )
    text = response.text.lower()

    assert response.verified is True
    assert "unknown" in text
    assert "no active weather alerts" not in text
    assert "INSUFFICIENT_DATA" in response.text


def test_the_apology_remains_when_there_is_nothing_to_compose_from():
    """No agent results at all -> nothing to state. The apology is still the
    honest answer, and it stays unverified so no badge is rendered."""
    response = SynthesisAgent(llm_client=_dead_client()).compose(
        _plan(), {"ocean": None, "geofencing": None}, language="en"
    )

    assert response.verified is False
    assert response.citations == []


# --------------------------------------------------------------------------- #
# #143 — cache successful compositions.
#
# The model misses often enough that an identical repeat question should not
# re-roll the same dice. Two rules matter more than the caching: only SAFE
# output is stored, and the fallback is never stored.
# --------------------------------------------------------------------------- #


def test_an_identical_query_reuses_the_composition_without_calling_the_llm():
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = _fake_llm_response(
        [{"text": "Conditions are CAUTION.", "source": "risk_safety"}]
    )
    agent = SynthesisAgent(llm_client=fake_client)

    first = agent.compose(_plan(), _caution_results(), language="en")
    second = agent.compose(_plan(), _caution_results(), language="en")

    assert fake_client.models.generate_content.call_count == 1
    assert second.text == first.text
    assert {c.source for c in second.citations} == {c.source for c in first.citations}


def test_different_agent_data_is_not_served_from_the_cache():
    """The key is the whole payload, including each agent's data_timestamp, so
    a hit means the underlying data is genuinely unchanged — not merely a
    similar-looking question. Getting this wrong would replay a stale verdict."""
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = _fake_llm_response(
        [{"text": "Conditions are CAUTION.", "source": "risk_safety"}]
    )
    agent = SynthesisAgent(llm_client=fake_client)

    agent.compose(_plan(), _caution_results(), language="en")
    changed = {
        "risk_safety": RiskVerdict(
            verdict="UNSAFE",  # different data -> must not reuse the CAUTION answer
            rationale="Location is within a Marine Protected Area.",
            contributing_factors=[],
        ),
    }
    fake_client.models.generate_content.return_value = _fake_llm_response(
        [{"text": "It is UNSAFE to fish here.", "source": "risk_safety"}]
    )
    second = agent.compose(_plan(), changed, language="en")

    assert fake_client.models.generate_content.call_count == 2
    assert "UNSAFE" in second.text


def test_a_rejected_composition_is_never_cached():
    """FR-SYN-2: output that failed the safety checks must not be stored, or a
    cache hit would replay an uncited claim we already refused to ship."""
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = _fake_llm_response(
        [{"text": "Everything looks fine.", "source": "ocean"}]  # uncited source
    )
    agent = SynthesisAgent(llm_client=fake_client)

    agent.compose(_plan(), _caution_results(), language="en")
    fake_client.models.generate_content.reset_mock()
    second = agent.compose(_plan(), _caution_results(), language="en")

    # It tried again rather than replaying the rejected sentences.
    assert fake_client.models.generate_content.call_count > 0
    assert "Everything looks fine" not in second.text


def test_the_fallback_is_never_cached():
    """Caching a degraded answer would freeze a bad outcome for the whole TTL
    and suppress the retry that might have succeeded. When the LLM recovers,
    the next identical query must get the real composition."""
    fake_client = MagicMock()
    fake_client.models.generate_content.side_effect = RuntimeError("gemini down")
    agent = SynthesisAgent(llm_client=fake_client)

    first = agent.compose(_plan(), _caution_results(), language="en")
    assert first.verified is True  # deterministic composition (#139)

    # The model comes back.
    fake_client.models.generate_content.side_effect = None
    fake_client.models.generate_content.return_value = _fake_llm_response(
        [{"text": "Conditions are CAUTION today.", "source": "risk_safety"}]
    )
    second = agent.compose(_plan(), _caution_results(), language="en")

    assert "Conditions are CAUTION today." in second.text



def test_map_payload_pfz_only():
    """PFZResult present, geofencing absent -> queried-location marker +
    nearest-PFZ marker, no geofence-related marker, zones stays empty (no
    dataclass here carries polygon geometry — see _build_map_payload
    docstring)."""
    results = _full_results(
        geofencing=None,
        ocean=PFZResult(
            centroid=LatLon(lat=9.85, lon=76.30),
            distance_km=12.4,
            bearing_deg=45.0,
            data_timestamp=datetime(2026, 9, 6, 17, 21, tzinfo=UTC),
            is_stale=False,
        ),
    )

    response = SynthesisAgent(llm_client=_dead_client()).compose(
        _plan_with_location(), results, language="en"
    )

    marker_ids = {m["id"] for m in response.map_payload.markers}
    assert "queried-location" in marker_ids
    assert "nearest-pfz" in marker_ids
    assert "geofence-violation" not in marker_ids
    assert "geofence-proximity" not in marker_ids
    assert response.map_payload.zones == []

    pfz_marker = next(m for m in response.map_payload.markers if m["id"] == "nearest-pfz")
    assert pfz_marker["lat"] == 9.85
    assert pfz_marker["lng"] == 76.30


def test_map_payload_geofence_only():
    """GeofenceResult present (violation), ocean/PFZ absent -> queried-location
    marker + geofence-violation marker, styled with isViolation, no PFZ
    marker."""
    results = _full_results(
        ocean=None,
        geofencing=GeofenceResult(
            within_imbl_buffer=False,
            imbl_distance_km=180.0,
            within_mpa=True,
            mpa_name="Gulf of Mannar",
        ),
    )

    response = SynthesisAgent(llm_client=_dead_client()).compose(
        _plan_with_location(), results, language="en"
    )

    marker_ids = {m["id"] for m in response.map_payload.markers}
    assert "queried-location" in marker_ids
    assert "geofence-violation" in marker_ids
    assert "nearest-pfz" not in marker_ids

    violation_marker = next(
        m for m in response.map_payload.markers if m["id"] == "geofence-violation"
    )
    assert violation_marker["isViolation"] is True
    assert "Gulf of Mannar" in violation_marker["label"]


def test_map_payload_both_pfz_and_geofence():
    """Both PFZResult and a geofence proximity (not violation) present ->
    all three markers show up: queried-location, nearest-pfz, and
    geofence-proximity (not geofence-violation, since within_mpa/
    within_imbl_buffer are both False here)."""
    results = _full_results(
        ocean=PFZResult(
            centroid=LatLon(lat=9.80, lon=76.25),
            distance_km=8.1,
            bearing_deg=200.0,
            data_timestamp=datetime(2026, 9, 6, 17, 21, tzinfo=UTC),
            is_stale=True,
        ),
        geofencing=GeofenceResult(
            within_imbl_buffer=False,
            imbl_distance_km=42.7,
            within_mpa=False,
        ),
    )

    response = SynthesisAgent(llm_client=_dead_client()).compose(
        _plan_with_location(), results, language="en"
    )

    marker_ids = {m["id"] for m in response.map_payload.markers}
    assert marker_ids == {"queried-location", "nearest-pfz", "geofence-proximity"}

    pfz_marker = next(m for m in response.map_payload.markers if m["id"] == "nearest-pfz")
    assert "stale" in pfz_marker["label"].lower()

    proximity_marker = next(
        m for m in response.map_payload.markers if m["id"] == "geofence-proximity"
    )
    assert proximity_marker["isProximity"] is True
    assert "42.7" in proximity_marker["label"]


def test_map_payload_neither_pfz_nor_geofence_current_behavior_preserved():
    """Neither PFZResult nor GeofenceResult present -> only the
    queried-location marker (if a location was resolved), zones always
    empty. This is the pre-#68 baseline behavior and must not regress."""
    results = _full_results(ocean=None, geofencing=None)

    response = SynthesisAgent(llm_client=_dead_client()).compose(
        _plan_with_location(), results, language="en"
    )

    marker_ids = {m["id"] for m in response.map_payload.markers}
    assert marker_ids == {"queried-location"}
    assert response.map_payload.zones == []


def test_map_payload_no_location_resolved_yields_no_queried_location_marker():
    """If the plan never resolved a location (e.g. an informational-only
    query with no invocations), _queried_location() returns None and no
    queried-location marker is added — must not crash."""
    results = _full_results(ocean=None, geofencing=None)

    response = SynthesisAgent(llm_client=_dead_client()).compose(
        _plan(), results, language="en"  # _plan(), not _plan_with_location()
    )

    marker_ids = {m["id"] for m in response.map_payload.markers}
    assert "queried-location" not in marker_ids
    assert response.map_payload.zones == []