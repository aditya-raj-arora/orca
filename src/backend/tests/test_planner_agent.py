"""
Fixture-based unit tests for the Planner's routing logic (LLD Fig.1).

Owner: P1. route_query() is a pure function (no LLM, no I/O) specifically so
these tests don't need a real LLM_API_KEY — see planner_agent.py's module
docstring.
"""
from app.core.session import ConversationContext
from app.orchestration.planner_agent import (
    MIN_ENTITY_CONFIDENCE,
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


# --------------------------------------------------------------------- #
# LLM-down keyword fallback (LLD §6 "LLM provider timeout during entity
# extraction"). Added with #36: this path had no coverage at all, which is
# how it went unnoticed that a fixed sub-threshold confidence made it
# unreachable — plan() bailed to the clarifying question before ever reading
# the intent flags it had just computed.
# --------------------------------------------------------------------- #
class _DownLLM:
    """A client whose only method fails, standing in for a provider timeout."""

    class models:  # noqa: N801 - mirrors google-genai's client.models attribute
        @staticmethod
        def generate_content(**_kwargs):
            raise TimeoutError("stub: LLM provider timed out")


def _planner_with_llm_down() -> PlannerAgent:
    return PlannerAgent(llm_client=_DownLLM())


def test_keyword_fallback_classifies_intent_when_the_llm_is_down():
    entities = _planner_with_llm_down().extract_entities(
        NormalizedQuery(text="is it safe to fish near the restricted zone", language="en")
    )

    assert entities.intent_safety and entities.intent_fishing and entities.intent_boundary
    # Location has no non-LLM fallback — it must stay unresolved, not be guessed.
    assert entities.location_resolvable is False
    assert entities.lat is None and entities.lon is None
    # Above the clarification threshold, so plan() can actually use the flags.
    assert entities.confidence >= MIN_ENTITY_CONFIDENCE


def test_keyword_fallback_with_no_match_stays_below_the_clarification_threshold():
    entities = _planner_with_llm_down().extract_entities(
        NormalizedQuery(text="what is the capital of france", language="en")
    )

    assert not (entities.intent_safety or entities.intent_fishing or entities.intent_boundary)
    assert entities.confidence < MIN_ENTITY_CONFIDENCE


def test_llm_down_with_no_prior_location_still_asks_for_one():
    """The fallback must not become a licence to guess: with nothing in the
    session and no geocoder, Figure 1's clarifying follow-up is still the
    correct outcome."""
    plan = _planner_with_llm_down().plan(
        NormalizedQuery(text="is it safe to fish today", language="en"),
        ConversationContext(session_id="llm-down"),
    )

    assert plan.needs_clarification
    assert plan.invocations == []


def test_llm_down_routes_normally_when_the_session_already_has_a_location():
    """LLD §6's second clause: keyword-extracted intent plus an FR-PLAN-5
    context location is enough to answer with the LLM unavailable."""
    context = ConversationContext(session_id="llm-down-with-context")
    context.append_turn("", {"location": LOCATION})

    plan = _planner_with_llm_down().plan(
        NormalizedQuery(text="is it safe to fish there", language="en"), context
    )

    assert not plan.needs_clarification
    assert _agent_names(plan) == ["weather", "ocean", "risk_safety"]
