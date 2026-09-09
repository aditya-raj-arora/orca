"""
Fixture-based unit tests for the Planner's routing logic (LLD Fig.1).

Owner: P1. route_query() is a pure function (no LLM, no I/O) specifically so
these tests don't need a real LLM_API_KEY — see planner_agent.py's module
docstring.
"""
from app.core.session import ConversationContext
from app.orchestration.planner_agent import (
    NormalizedQuery,
    PlannerAgent,
    QueryEntities,
    route_query,
)

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
# issue #174 — area-scoped ("which zones/regions") queries
# --------------------------------------------------------------------- #
def test_scope_defaults_to_point_and_is_not_passed_when_point():
    plan = route_query(_entities(intent_fishing=True, intent_boundary=True), LOCATION, [])
    for inv in plan.invocations:
        if inv.agent_name in ("ocean", "geofencing"):
            assert inv.input_payload.get("scope") == "point"


def test_area_scope_is_passed_to_ocean_and_geofencing_payloads():
    plan = route_query(
        _entities(intent_fishing=True, intent_boundary=True, scope="area"), LOCATION, []
    )
    payloads = {inv.agent_name: inv.input_payload for inv in plan.invocations}
    assert payloads["ocean"]["scope"] == "area"
    assert payloads["geofencing"]["scope"] == "area"
    assert any("area-scoped" in step for step in plan.trace)


def test_keyword_fallback_flags_which_zones_query_as_area():
    ents = PlannerAgent()._extract_via_keywords(
        NormalizedQuery(
            text="which fishing zones should be avoided due to restricted areas",
            language="en",
        )
    )
    assert ents.scope == "area"
    assert ents.intent_boundary is True


def test_keyword_fallback_leaves_single_point_query_as_point():
    ents = PlannerAgent()._extract_via_keywords(
        NormalizedQuery(text="is it safe to fish near Kochi tomorrow", language="en")
    )
    assert ents.scope == "point"


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
