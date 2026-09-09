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
        intent_weather=False,
        intent_safety=False,
        intent_fishing=False,
        intent_boundary=False,
        confidence=0.9,
    )
    defaults.update(overrides)
    return QueryEntities(**defaults)


def _agent_names(plan) -> list[str]:
    return [inv.agent_name for inv in plan.invocations]


def test_safety_intent_invokes_weather_geofencing_and_risk_safety():
    """#112: safety intent must invoke Geofencing too, even with no boundary
    words in the query. Figure 2 cannot reach ANY verdict without a geofence
    (RiskSafetyAgent returns INSUFFICIENT_DATA when it is None), so omitting it
    made every "is it safe to..." query permanently unanswerable."""
    plan = route_query(_entities(intent_safety=True), LOCATION, [])
    assert _agent_names(plan) == ["weather", "geofencing", "risk_safety"]


def test_fishing_intent_alone_does_not_invoke_geofencing():
    """The #112 widening is scoped to safety intent — a pure "where are the
    fish" question still doesn't need boundary data, so it shouldn't pay for
    the extra adapter call."""
    plan = route_query(_entities(intent_fishing=True), LOCATION, [])
    assert "geofencing" not in _agent_names(plan)


def test_geofencing_is_invoked_once_when_a_query_is_both_safety_and_boundary():
    plan = route_query(_entities(intent_safety=True, intent_boundary=True), LOCATION, [])
    assert _agent_names(plan).count("geofencing") == 1
    assert _agent_names(plan) == ["weather", "geofencing", "risk_safety"]


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


# --------------------------------------------------------------------- #
# #126: intent_weather is informational-only — Weather Agent runs, but no
# verdict — distinct from intent_safety's Weather + Geofencing + Risk.
# --------------------------------------------------------------------- #


def test_weather_intent_alone_invokes_only_weather_no_risk_safety():
    plan = route_query(_entities(intent_weather=True), LOCATION, [])
    assert _agent_names(plan) == ["weather"]


def test_weather_intent_does_not_invoke_geofencing_or_risk_safety():
    """A plain 'what is the weather' question must not drag in the safety
    pipeline (#126) — only intent_safety widens routing to Geofencing+Risk."""
    plan = route_query(_entities(intent_weather=True), LOCATION, [])
    assert "geofencing" not in _agent_names(plan)
    assert "risk_safety" not in _agent_names(plan)


def test_weather_and_safety_intents_together_still_get_the_full_safety_pipeline():
    """Doesn't weaken #112: if a query is BOTH informational-weather and
    explicitly safety-framed, the safety pipeline still wins."""
    plan = route_query(_entities(intent_weather=True, intent_safety=True), LOCATION, [])
    assert _agent_names(plan) == ["weather", "geofencing", "risk_safety"]


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


def test_route_query_is_flagged_on_the_plan():
    """#173 interim fix: no route concept exists, so route_query() must at
    least flag the gap on the plan for SynthesisAgent to caveat, rather than
    silently answering as if the single resolved point covered the journey."""
    plan = route_query(_entities(intent_safety=True, route_query=True), LOCATION, [])
    assert plan.route_query_detected is True
    assert any("route" in step.lower() for step in plan.trace)


def test_non_route_query_does_not_set_the_flag():
    plan = route_query(_entities(intent_safety=True), LOCATION, [])
    assert plan.route_query_detected is False


def test_keyword_fallback_flags_route_phrased_queries():
    ents = PlannerAgent()._extract_via_keywords(
        NormalizedQuery(text="what is the safest route from Kochi to Colombo", language="en")
    )
    assert ents.route_query is True


def test_keyword_fallback_leaves_plain_location_queries_unflagged():
    ents = PlannerAgent()._extract_via_keywords(
        NormalizedQuery(text="is it safe to fish near Kochi tomorrow", language="en")
    )
    assert ents.route_query is False


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
    assert not entities.intent_weather
    assert entities.confidence < MIN_ENTITY_CONFIDENCE


def test_keyword_fallback_classifies_a_plain_weather_question_as_informational():
    """#126: a plain weather question, with no safety framing, must classify
    as intent_weather (informational) even in degraded (LLM-down) mode."""
    entities = _planner_with_llm_down().extract_entities(
        NormalizedQuery(text="what is the weather in chennai", language="en")
    )

    assert entities.intent_weather is True
    assert entities.intent_safety is False
    assert entities.confidence >= MIN_ENTITY_CONFIDENCE


def test_keyword_fallback_prefers_safety_over_weather_when_both_present():
    entities = _planner_with_llm_down().extract_entities(
        NormalizedQuery(text="is it safe with this wind and wave forecast", language="en")
    )

    assert entities.intent_safety is True
    assert entities.intent_weather is False


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
    # geofencing rides along on intent_safety (#112)
    assert _agent_names(plan) == ["weather", "ocean", "geofencing", "risk_safety"]


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
    assert _agent_names(plan) == ["weather", "geofencing", "risk_safety"]
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
