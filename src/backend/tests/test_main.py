"""
Gateway wiring tests (#30): session lifecycle, WS trace streaming +
final_response, non-streaming query, history.

Owner: P1. All agents faked via app.main._build_graph — no LLM/DB/external
API needed (mirrors tests/test_graph.py's injection pattern one level up).
Bhashini itself is a real (unimplemented) BhashiniClient in most of these,
which is the point: it exercises the degrade-to-English / FR-LANG-6 paths
that exist precisely because #31 isn't done yet.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

import app.main as gateway
from app.orchestration.graph import build_orchestration_graph
from app.schemas.geofence import GeofenceResult
from app.schemas.ocean import OceanParams, PFZResult
from app.schemas.risk import RiskVerdict
from app.schemas.synthesis import AgentInvocationRequest, ComposedResponse, ExecutionPlan
from app.schemas.weather import WeatherResult

LOCATION = {"place_name": "Kochi", "lat": 9.93, "lon": 76.26}


class FakePlanner:
    def plan(self, query, context):
        context.append_turn(query.text, {"location": LOCATION})
        return ExecutionPlan(
            invocations=[
                AgentInvocationRequest(
                    agent_name="weather",
                    input_payload={"location": LOCATION, "time_window_text": None},
                ),
                AgentInvocationRequest(agent_name="ocean", input_payload={"location": LOCATION}),
                AgentInvocationRequest(agent_name="risk_safety", input_payload={}),
            ],
            trace=["Planner: routing to Weather, Ocean"],
        )


class FakeWeather:
    def get_conditions(self, location, window):
        return WeatherResult(wind_speed_kmh=12.0, wave_height_m=0.8)


class FakeOcean:
    def get_nearest_pfz(self, location):
        return PFZResult(
            centroid=location,
            distance_km=4.2,
            bearing_deg=90.0,
            data_timestamp=datetime.now(UTC),
            is_stale=False,
        )

    def get_ocean_parameters(self, location):
        return OceanParams(sea_surface_temp_c=29.1, chlorophyll_mg_m3=0.3)


class FakeGeofencing:
    def check(self, location):
        return GeofenceResult(within_imbl_buffer=False, imbl_distance_km=50.0, within_mpa=False)


class FakeRisk:
    def evaluate(self, weather, geofence, ocean):
        return RiskVerdict(verdict="CAUTION", rationale="Moderate winds.", contributing_factors=[])


class FakeSynthesis:
    def compose(self, plan, results, language):
        return ComposedResponse(text="Conditions are moderate near Kochi.", citations=[])


def _fake_graph():
    return build_orchestration_graph(
        planner=FakePlanner(),
        weather_agent=FakeWeather(),
        ocean_agent=FakeOcean(),
        geofencing_agent=FakeGeofencing(),
        risk_agent=FakeRisk(),
        synthesis_agent=FakeSynthesis(),
    )


def _client(monkeypatch) -> TestClient:
    monkeypatch.setattr(gateway, "_build_graph", _fake_graph)
    gateway._SESSIONS.clear()
    return TestClient(gateway.app)


def test_create_session_returns_id_and_timestamp(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post("/api/v1/session")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"]
    assert body["created_at"]
    assert body["session_id"] in gateway._SESSIONS


def test_session_history_unknown_session_404(monkeypatch):
    client = _client(monkeypatch)
    resp = client.get("/api/v1/session/does-not-exist/history")
    assert resp.status_code == 404


def test_session_history_returns_turns_after_a_query(monkeypatch):
    client = _client(monkeypatch)
    client.post("/api/v1/query/s1", json={"mode": "text", "text": "conditions near Kochi?"})
    resp = client.get("/api/v1/session/s1/history")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_query_sync_text_mode_end_to_end(monkeypatch):
    client = _client(monkeypatch)
    # detect_language is real (Sarvam-backed, #153) and network-bound, so it's
    # mocked here rather than left to fall through to the SARVAM_API_KEY-unset
    # degrade path — this test exercises the happy path, not FR-LANG-6's
    # fallback (see test_query_sync_text_mode_degrades_to_english_on_bhashini_
    # failure for that). Returns the real BCP-47 code, matching TranscriptResult.
    monkeypatch.setattr(gateway.BhashiniClient, "detect_language", lambda self, text: "en-IN")
    resp = client.post("/api/v1/query/s2", json={"mode": "text", "text": "conditions near Kochi?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == "Conditions are moderate near Kochi."
    assert body["verdict"] == "CAUTION"
    assert body["language"] == "en-IN"
    assert body["audio_base64"] is None  # mode=text, no TTS attempted


def test_query_sync_text_mode_degrades_to_english_on_bhashini_failure(monkeypatch):
    """FR-LANG-6: a text query can proceed without language detection, so a
    detect_language failure (no SARVAM_API_KEY, network error, etc.) degrades
    to English with a logged warning instead of failing the query."""
    client = _client(monkeypatch)

    def _explode(self, text):
        raise RuntimeError("SARVAM_API_KEY is not set")

    monkeypatch.setattr(gateway.BhashiniClient, "detect_language", _explode)
    resp = client.post("/api/v1/query/s2b", json={"mode": "text", "text": "conditions near Kochi?"})
    assert resp.status_code == 200
    assert resp.json()["language"] == "en"


def test_query_sync_voice_mode_without_bhashini_returns_503(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post("/api/v1/query/s3", json={"mode": "voice", "audio_base64": "aGVsbG8="})
    assert resp.status_code == 503


def test_websocket_streams_trace_then_final_response(monkeypatch):
    client = _client(monkeypatch)
    with client.websocket_connect("/ws/v1/query/s4") as ws:
        ws.send_json({"type": "query", "mode": "text", "text": "conditions near Kochi?"})
        messages = []
        while True:
            msg = ws.receive_json()
            messages.append(msg)
            if msg["type"] == "final_response":
                break

    trace_updates = [m for m in messages if m["type"] == "trace_update"]
    finals = [m for m in messages if m["type"] == "final_response"]
    assert len(finals) == 1
    assert finals[0]["text"] == "Conditions are moderate near Kochi."
    assert finals[0]["verdict"] == "CAUTION"

    # Planner's trace must be first (everything else depends on the plan);
    # order among the parallel specialists themselves isn't asserted.
    assert trace_updates[0]["step"] == "Planner: routing to Weather, Ocean"
    steps = {m["step"] for m in trace_updates}
    assert any("Weather Agent" in s for s in steps)
    assert any("Ocean Agent" in s for s in steps)


def test_websocket_malformed_message_sends_error(monkeypatch):
    client = _client(monkeypatch)
    with client.websocket_connect("/ws/v1/query/s5") as ws:
        ws.send_text("not json")
        msg = ws.receive_json()
        assert msg["type"] == "error"


# --------------------------------------------------------------------------- #
# #121: a verdict must never be shown without an explanation behind it
# --------------------------------------------------------------------------- #
def _risk(verdict: str):
    from app.schemas.risk import RiskVerdict

    return RiskVerdict(verdict=verdict, rationale="stub", contributing_factors=[])


def test_failed_synthesis_does_not_ship_an_unexplained_safe_verdict():
    """The deployed UI rendered a green SAFE badge above "Sorry, I couldn't put
    together an answer" for a Kochi->Colombo route query. A verdict with no
    supporting reasoning is exactly what FR-SYN-2 and SynthesisAgent's
    refuse-to-ship checks exist to prevent, and the Gateway was undoing them."""
    from app.main import _final_response
    from app.orchestration.graph import _unavailable_composed_response

    state = {
        "results": {"risk_safety": _risk("SAFE")},
        "composed": _unavailable_composed_response(),
        "synthesis_ok": False,
    }
    assert _final_response(state, "en", None).verdict == "INSUFFICIENT_DATA"


def test_successful_synthesis_still_reports_the_real_verdict():
    from app.main import _final_response
    from app.schemas.synthesis import ComposedResponse

    state = {
        "results": {"risk_safety": _risk("SAFE")},
        "composed": ComposedResponse(text="Conditions are calm."),
        "synthesis_ok": True,
    }
    assert _final_response(state, "en", None).verdict == "SAFE"


def test_absent_flag_defaults_to_trusting_the_verdict():
    """Back-compat for any state dict that predates the flag (and for the
    clarification path, which sets it True explicitly)."""
    from app.main import _final_response
    from app.schemas.synthesis import ComposedResponse

    state = {
        "results": {"risk_safety": _risk("CAUTION")},
        "composed": ComposedResponse(text="Be careful."),
    }
    assert _final_response(state, "en", None).verdict == "CAUTION"


def test_unsafe_verdict_is_also_withheld_when_unexplained():
    """Not just the reassuring ones: an UNSAFE verdict the system cannot
    justify is still a claim it should not make."""
    from app.main import _final_response
    from app.orchestration.graph import _unavailable_composed_response

    state = {
        "results": {"risk_safety": _risk("UNSAFE")},
        "composed": _unavailable_composed_response(),
        "synthesis_ok": False,
    }
    assert _final_response(state, "en", None).verdict == "INSUFFICIENT_DATA"


# --------------------------------------------------------------------------- #
# #126: an informational-only query (no Risk/Safety invocation at all) must
# report verdict=None, not "INSUFFICIENT_DATA" — that value means Risk/Safety
# WAS asked and couldn't reach one, which is a different claim than "no
# verdict was ever requested".
# --------------------------------------------------------------------------- #


def test_informational_query_reports_no_verdict_not_insufficient_data():
    from app.main import _final_response
    from app.schemas.synthesis import ComposedResponse

    state = {
        "results": {"weather": object()},  # risk_safety never invoked
        "composed": ComposedResponse(text="It's 28°C with light winds near Kochi."),
        "synthesis_ok": True,
    }
    assert _final_response(state, "en", None).verdict is None


def test_informational_query_with_no_results_at_all_also_reports_no_verdict():
    from app.main import _final_response
    from app.schemas.synthesis import ComposedResponse

    state = {"composed": ComposedResponse(text="Clarifying question or similar.")}
    assert _final_response(state, "en", None).verdict is None


# --------------------------------------------------------------------------- #
# #141 — warm the LLM clients at startup.
#
# The deployed Planner call 504'd on its deadline while measuring 0.9s against
# the same API from a laptop. The query was the first on a 36-second-old
# container, and both agents import google.genai INSIDE _build_llm_client(),
# so it was paying for a package import on the user's clock.
# --------------------------------------------------------------------------- #


def test_startup_warms_the_llm_clients(monkeypatch):
    """Construction AND a real round trip, before anyone is waiting.

    #141 warmed only the import and the client object, which is why it didn't
    fix the cold first query: constructing a client opens no socket, and DNS +
    TCP + TLS is the expensive half (#149)."""
    warmed: list[str] = []

    class _FakeClient:
        class models:  # noqa: N801 - mirrors the SDK's attribute shape
            @staticmethod
            def generate_content(**_kwargs):
                warmed.append("round-trip")

    monkeypatch.setattr(gateway, "_build_graph", lambda: warmed.append("graph"))
    monkeypatch.setattr(
        gateway.PlannerAgent, "_build_llm_client", lambda self: _FakeClient()
    )

    with TestClient(gateway.app):  # entering the context runs startup
        pass

    assert warmed == ["graph", "round-trip"]


def test_startup_survives_a_failing_warm_up(monkeypatch):
    """Warming is an optimisation. A Gateway that refuses to boot because an
    LLM client couldn't be constructed is strictly worse than one that serves
    a slower first query — every agent already degrades on its own (LLD §6)."""
    def _explode(self):
        raise RuntimeError("no API key in this environment")

    monkeypatch.setattr(gateway.PlannerAgent, "_build_llm_client", _explode)

    with TestClient(gateway.app) as client:
        assert client.get("/healthz").status_code == 200


def test_startup_applies_the_configured_log_level(monkeypatch):
    """#143: core/config.py has declared log_level since it was written and
    nothing ever read it, so the effective level was the root default of
    WARNING and every logger.info() in the codebase was invisible in
    production — including the line naming the component that answers a large
    share of queries."""
    import logging

    app_logger = logging.getLogger("app")
    original = app_logger.level
    app_logger.setLevel(logging.NOTSET)
    try:
        with TestClient(gateway.app):
            assert app_logger.level == logging.INFO
    finally:
        app_logger.setLevel(original)


def test_an_unknown_log_level_does_not_break_startup(monkeypatch):
    """A typo'd env var must not take the Gateway down — logging.getLevelName
    returns a string for an unknown name, which setLevel would reject."""

    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "log_level", "NOT_A_LEVEL", raising=False)

    with TestClient(gateway.app) as client:
        assert client.get("/healthz").status_code == 200


def test_startup_actually_emits_an_info_record(capsys):
    """#145: the real assertion. #144 set the logger's level and changed
    nothing on the deployed instance, because a level only decides which
    records a logger CREATES — emitting them is a handler's job, and under
    uvicorn the root logger has none, so everything went to
    logging.lastResort, which is pinned at WARNING.

    test_startup_applies_the_configured_log_level passes against that broken
    state, which is exactly why it did not catch it. Assert the OUTPUT."""
    import logging

    # Drop any handler a previous test's startup installed: it holds the
    # stderr from back then, not the one capsys is capturing now.
    app_logger = logging.getLogger("app")
    for handler in list(app_logger.handlers):
        if getattr(handler, gateway._ORCA_HANDLER_FLAG, False):
            app_logger.removeHandler(handler)

    with TestClient(gateway.app):
        logging.getLogger("app.orchestration.synthesis_agent").info("info-line-marker")

    assert "info-line-marker" in capsys.readouterr().err


def test_the_log_handler_is_not_installed_twice(capsys):
    """Startup runs many times across this suite and once per process in
    production. A second handler would double every line."""
    import logging

    app_logger = logging.getLogger("app")
    for handler in list(app_logger.handlers):
        if getattr(handler, gateway._ORCA_HANDLER_FLAG, False):
            app_logger.removeHandler(handler)

    with TestClient(gateway.app):
        pass
    with TestClient(gateway.app):
        logging.getLogger("app.orchestration.graph").info("once-only-marker")

    assert capsys.readouterr().err.count("once-only-marker") == 1


# --------------------------------------------------------------------------- #
# #149 — the first query after a cold start used to time out.
#
# build_orchestration_graph() default-constructs fresh agents, and _build_graph()
# ran per request, so every query built a new LLM client with a new httpx
# connection pool: a fresh DNS + TCP + TLS handshake each time. On a throttled
# instance the first one exceeded even the 25s extraction deadline.
# --------------------------------------------------------------------------- #


def test_the_compiled_graph_is_built_once_and_reused():
    """Per-request construction is what discarded the warmed client before the
    first query could use it."""
    builds = {"n": 0}

    def _counting_build():
        builds["n"] += 1
        return object()

    import app.orchestration.graph as graph_module

    original = graph_module.build_orchestration_graph
    gateway.build_orchestration_graph = _counting_build
    try:
        first = gateway._build_graph()
        second = gateway._build_graph()
    finally:
        gateway.build_orchestration_graph = original

    assert builds["n"] == 1
    assert first is second


def test_every_planner_instance_shares_one_llm_client(monkeypatch):
    """Instances are cheap and come and go; the connection pool must not."""
    from app.orchestration import planner_agent

    constructed = {"n": 0}

    def _construct(self):
        constructed["n"] += 1
        return object()

    monkeypatch.setattr(planner_agent.PlannerAgent, "_construct_llm_client", _construct)

    first = planner_agent.PlannerAgent()._build_llm_client()
    second = planner_agent.PlannerAgent()._build_llm_client()  # a DIFFERENT instance

    assert constructed["n"] == 1
    assert first is second


def test_every_synthesis_instance_shares_one_llm_client(monkeypatch):
    """Same as the Planner: the graph builds a fresh SynthesisAgent, so a
    per-instance client meant a per-query connection pool."""
    from app.orchestration import synthesis_agent

    constructed = {"n": 0}

    def _construct(self):
        constructed["n"] += 1
        return object()

    monkeypatch.setattr(
        synthesis_agent.SynthesisAgent, "_construct_llm_client", _construct
    )

    first = synthesis_agent.SynthesisAgent()._build_llm_client()
    second = synthesis_agent.SynthesisAgent()._build_llm_client()  # DIFFERENT instance

    assert constructed["n"] == 1
    assert first is second
