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
    resp = client.post("/api/v1/query/s2", json={"mode": "text", "text": "conditions near Kochi?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == "Conditions are moderate near Kochi."
    assert body["verdict"] == "CAUTION"
    assert body["language"] == "en"  # BhashiniClient.detect_language unimplemented -> degrades
    assert body["audio_base64"] is None  # mode=text, no TTS attempted


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
# #141 — warm the LLM clients at startup.
#
# The deployed Planner call 504'd on its deadline while measuring 0.9s against
# the same API from a laptop. The query was the first on a 36-second-old
# container, and both agents import google.genai INSIDE _build_llm_client(),
# so it was paying for a package import on the user's clock.
# --------------------------------------------------------------------------- #


def test_startup_warms_the_llm_clients(monkeypatch):
    """The import and client construction happen before anyone is waiting."""
    warmed: list[str] = []

    monkeypatch.setattr(gateway, "_build_graph", lambda: warmed.append("graph"))
    monkeypatch.setattr(
        gateway.PlannerAgent, "_build_llm_client", lambda self: warmed.append("planner")
    )
    monkeypatch.setattr(
        gateway.SynthesisAgent, "_build_llm_client", lambda self: warmed.append("synthesis")
    )

    with TestClient(gateway.app):  # entering the context runs startup
        pass

    assert warmed == ["graph", "planner", "synthesis"]


def test_startup_survives_a_failing_warm_up(monkeypatch):
    """Warming is an optimisation. A Gateway that refuses to boot because an
    LLM client couldn't be constructed is strictly worse than one that serves
    a slower first query — every agent already degrades on its own (LLD §6)."""
    def _explode(self):
        raise RuntimeError("no API key in this environment")

    monkeypatch.setattr(gateway.PlannerAgent, "_build_llm_client", _explode)

    with TestClient(gateway.app) as client:
        assert client.get("/healthz").status_code == 200
