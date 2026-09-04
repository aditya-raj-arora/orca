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


# --------------------------------------------------------------------------- #
# Location must carry coordinates, not just a name (#110)
#
# Regression guard for the bug where the LLM returned a place NAME with null
# lat/lon, `as_location_dict()` returned a truthy dict, plan()'s `is None`
# gate let it through, and every specialist agent then reported 'unavailable'
# WITHOUT calling its adapter — for every query in the session.
# --------------------------------------------------------------------------- #
class _StubGeocoder:
    """Stands in for GeocodingAdapter. `location` None means "cannot resolve"."""

    def __init__(self, location=None):
        self._location = location
        self.calls: list[str] = []

    def fetch(self, params):
        from datetime import UTC, datetime

        from app.data_access.base import AdapterResult

        self.calls.append(params.get("place_name"))
        status = "ok" if self._location else "unavailable"
        return AdapterResult(
            data=dict(self._location) if self._location else None,
            fetched_at=datetime.now(UTC),
            status=status,
        )


def _name_only_llm(place_name="Kochi"):
    """An LLM that names a place but returns null coordinates — the shape the
    extraction prompt invites, since it asks for lat/lon only 'if you know it'."""

    class _LLM:
        class models:  # noqa: N801 - mirrors google-genai's client.models
            @staticmethod
            def generate_content(**_kwargs):
                class _R:
                    text = (
                        f'{{"location_resolvable":true,"place_name":"{place_name}",'
                        '"lat":null,"lon":null,"time_window_text":null,'
                        '"intent_safety":true,"intent_fishing":false,'
                        '"intent_boundary":false,"intent_keywords":["safe"],'
                        '"confidence":0.9}'
                    )

                return _R()

    return _LLM()


def test_usable_location_rejects_anything_without_real_coordinates():
    from app.orchestration.planner_agent import _usable_location

    assert _usable_location(LOCATION) == LOCATION
    assert _usable_location({"place_name": "Kochi", "lat": None, "lon": None}) is None
    assert _usable_location({"place_name": "Kochi", "lat": 9.93}) is None
    assert _usable_location({"lat": "9.93", "lon": "76.26"}) is None  # strings, not numbers
    assert _usable_location({"lat": True, "lon": False}) is None      # bool is an int subclass
    assert _usable_location({"lat": 91.0, "lon": 76.26}) is None      # out of range
    assert _usable_location({"lat": 9.93, "lon": 181.0}) is None
    assert _usable_location(None) is None
    assert _usable_location({}) is None


def test_place_name_without_coordinates_is_geocoded_then_routed():
    geocoder = _StubGeocoder(LOCATION)
    plan = PlannerAgent(llm_client=_name_only_llm(), geocoder=geocoder).plan(
        NormalizedQuery(text="is it safe to fish near Kochi", language="en"),
        ConversationContext(session_id="geocode-ok"),
    )

    assert geocoder.calls == ["Kochi"]
    assert not plan.needs_clarification
    assert _agent_names(plan) == ["weather", "risk_safety"]
    # The coordinates the specialist agents actually need must be on the payload.
    payload_location = plan.invocations[0].input_payload["location"]
    assert (payload_location["lat"], payload_location["lon"]) == (9.93, 76.26)


def test_unresolvable_place_name_asks_the_clarifying_question():
    """Figure 1's 'Location resolvable? = No' branch must fire when we cannot
    get coordinates — previously it could not, because a name-only dict is
    truthy, so the user was never asked for a usable location."""
    plan = PlannerAgent(llm_client=_name_only_llm("Atlantis"), geocoder=_StubGeocoder(None)).plan(
        NormalizedQuery(text="is it safe to fish near Atlantis", language="en"),
        ConversationContext(session_id="geocode-fail"),
    )

    assert plan.needs_clarification
    assert plan.invocations == []


def test_a_coordinateless_location_is_never_stored_in_context():
    """The session-poisoning half of the bug: once a name-only location was
    recorded, last_known_location() returned it for every follow-up (truthy),
    so FR-PLAN-5 turns failed identically for the rest of the session."""
    context = ConversationContext(session_id="no-poison")
    PlannerAgent(llm_client=_name_only_llm(), geocoder=_StubGeocoder(None)).plan(
        NormalizedQuery(text="is it safe to fish near Kochi", language="en"), context
    )
    assert context.last_known_location() is None


def test_a_coordinateless_context_location_is_ignored_not_reused():
    """Defence in depth for sessions/turns written by anything else: a stored
    location without coordinates must not satisfy Figure 1."""
    context = ConversationContext(session_id="legacy-poisoned")
    context.append_turn("", {"location": {"place_name": "Kochi", "lat": None, "lon": None}})

    plan = _planner_with_llm_down().plan(
        NormalizedQuery(text="is it safe to fish there", language="en"), context
    )

    assert plan.needs_clarification
    assert plan.invocations == []


def test_geocoder_failure_degrades_to_clarification_and_never_raises():
    class _ExplodingGeocoder:
        def fetch(self, params):
            raise RuntimeError("stub: geocoder exploded")

    plan = PlannerAgent(llm_client=_name_only_llm(), geocoder=_ExplodingGeocoder()).plan(
        NormalizedQuery(text="is it safe to fish near Kochi", language="en"),
        ConversationContext(session_id="geocode-boom"),
    )

    assert plan.needs_clarification
    assert plan.invocations == []


def test_coordinates_from_the_llm_skip_the_geocoder():
    """No wasted call (and no quota spend) when extraction already gave us
    usable coordinates."""
    geocoder = _StubGeocoder(LOCATION)

    class _LLM:
        class models:  # noqa: N801
            @staticmethod
            def generate_content(**_kwargs):
                class _R:
                    text = (
                        '{"location_resolvable":true,"place_name":"Kochi","lat":9.93,'
                        '"lon":76.26,"time_window_text":null,"intent_safety":true,'
                        '"intent_fishing":false,"intent_boundary":false,'
                        '"intent_keywords":["safe"],"confidence":0.9}'
                    )

                return _R()

    plan = PlannerAgent(llm_client=_LLM(), geocoder=geocoder).plan(
        NormalizedQuery(text="is it safe to fish near Kochi", language="en"),
        ConversationContext(session_id="already-resolved"),
    )

    assert geocoder.calls == []
    assert not plan.needs_clarification
