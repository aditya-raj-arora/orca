"""
Fixture-based unit tests for the Planner's routing logic (LLD Fig.1).

Owner: P1. route_query() is a pure function (no LLM, no I/O) specifically so
these tests don't need a real LLM_API_KEY — see planner_agent.py's module
docstring.
"""
from app.core.session import ConversationContext
from app.orchestration.planner_agent import QueryEntities, route_query

LOCATION = {"place_name": "Kochi", "lat": 9.93, "lon": 76.26}


def _entities(**overrides) -> QueryEntities:
    defaults = dict(
        location_resolvable=True,
        place_name="Kochi",
        lat=9.93,
        lon=76.26,
        time_window_text=None,
        intent_safety=False,
        intent_fishing=False,
        intent_boundary=False,
        confidence=0.9,
    )
    defaults.update(overrides)
    return QueryEntities(**defaults)


def _agent_names(plan) -> list[str]:
    return [inv.agent_name for inv in plan.invocations]


def test_safety_intent_invokes_weather_and_risk_safety():
    plan = route_query(_entities(intent_safety=True), LOCATION, [])
    assert _agent_names(plan) == ["weather", "risk_safety"]


def test_fishing_intent_invokes_ocean_and_risk_safety():
    plan = route_query(_entities(intent_fishing=True), LOCATION, [])
    assert _agent_names(plan) == ["ocean", "risk_safety"]


def test_boundary_intent_invokes_geofencing_and_risk_safety():
    plan = route_query(_entities(intent_boundary=True), LOCATION, [])
    assert _agent_names(plan) == ["geofencing", "risk_safety"]


def test_all_three_intents_invoke_all_four_agents_in_order():
    plan = route_query(
        _entities(intent_safety=True, intent_fishing=True, intent_boundary=True), LOCATION, []
    )
    assert _agent_names(plan) == ["weather", "ocean", "geofencing", "risk_safety"]


def test_no_intent_is_informational_only_no_risk_safety():
    plan = route_query(_entities(), LOCATION, [])
    assert plan.invocations == []
    assert not plan.needs_clarification


def test_trace_is_populated_for_fr_plan_4():
    plan = route_query(_entities(intent_safety=True), LOCATION, [])
    assert any("Weather Agent" in step for step in plan.trace)
    assert any("Risk/Safety Agent" in step for step in plan.trace)


# --------------------------------------------------------------------- #
# ConversationContext — used by plan() for the FR-PLAN-5 location fallback.
# --------------------------------------------------------------------- #


def test_last_known_location_returns_most_recent():
    ctx = ConversationContext(session_id="s1")
    ctx.append_turn("where are the fish near Kochi", {"location": LOCATION})
    ctx.append_turn("what about tomorrow", {"location": None})
    # follow-up turn didn't resolve a location; the most recent RESOLVED one wins
    assert ctx.last_known_location() == LOCATION


def test_last_known_location_none_when_never_resolved():
    ctx = ConversationContext(session_id="s2")
    ctx.append_turn("hello", {"location": None})
    assert ctx.last_known_location() is None
