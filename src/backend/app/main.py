"""
FastAPI Gateway — single entry point for REST and WebSocket traffic.

Owner: P1 (Backend/Orchestration Lead).
Supports: all FR groups (routes into Language + Orchestration layers).
Reference: HLD v1.0 §3 (Component Design), LLD v1.0 §2.8, §5 (API design).

Endpoints (LLD §5, HLD §5.1):
  POST /api/v1/session                       -> create a session
  WS   /ws/v1/query/{session_id}              -> streaming voice/text query
  POST /api/v1/query/{session_id}             -> non-streaming text query
  GET  /api/v1/session/{session_id}/history   -> prior conversation turns

Session persistence: app/db/session_repo.py's DB calls are all still
NotImplementedError pending PostgreSQL+PostGIS being stood up (#11, P4). This
module uses an in-memory _SESSIONS store as a stand-in so the query pipeline
(#30) can be wired and tested end-to-end without waiting on that — process-
local, lost on restart, fine for prototype scope (SRS §2.5 constraints,
core/session.py's own docstring notes the same). Swap for session_repo.py
once #11 lands.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import sys
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.session import ConversationContext
from app.language.bhashini_client import BhashiniClient, BhashiniUnavailableError
from app.orchestration import planner_agent as planner_module
from app.orchestration.graph import build_orchestration_graph
from app.orchestration.planner_agent import NormalizedQuery, PlannerAgent
from app.schemas.risk import RiskVerdict
from app.schemas.synthesis import ComposedResponse

logger = logging.getLogger(__name__)

# Marks the handler _apply_log_level() owns, so repeated startups adjust it
# instead of stacking duplicates (#145).
_ORCA_HANDLER_FLAG = "_orca_app_handler"

# Built once, reused for every request (#149) — see _build_graph().
_COMPILED_GRAPH: Any = None
_COMPILED_GRAPH_LOCK = threading.Lock()


def _apply_log_level() -> None:
    """Make core/config.py's `log_level` mean something (#143).

    It has been declared since the settings module was written and read by
    nothing — no basicConfig, no setLevel, no dictConfig — so the effective
    level was the root default of WARNING and every logger.info() in this
    codebase was invisible in production. That is not cosmetic: "Synthesis:
    composed N sentences without the LLM" is an INFO line, so the component
    answering a large share of queries left no trace at all, and its firing had
    to be inferred from the wording of the response.

    Scoped to the `app` namespace rather than the root logger on purpose —
    INFO on httpx/google-genai/uvicorn internals is noise we do not want, and
    turning it on for everything is how people end up ignoring logs.

    Setting the level is necessary and NOT sufficient (#145). A logger's level
    only decides which records it creates; emitting them is a handler's job,
    and under uvicorn the root logger has no handlers at all. Every record from
    `app.*` was therefore going to logging.lastResort, which is hard-coded to
    WARNING — so INFO passed the logger check and was dropped by the handler.
    #144 raised the logger level alone and changed nothing. Hence the explicit
    handler below.

    lastResort also writes a bare message with no level marker, which is why
    Render labelled our logger.error lines as "info" and made filtering by
    level useless. The formatter fixes that too.
    """
    level = logging.getLevelName(get_settings().log_level.upper())
    if not isinstance(level, int):  # unknown name -> getLevelName returns a str
        logger.warning(
            "Unknown log_level %r — leaving logging at its default",
            get_settings().log_level,
        )
        return

    app_logger = logging.getLogger("app")
    app_logger.setLevel(level)

    # Idempotent: startup runs many times across the test suite, and a second
    # handler would double every line.
    if not any(getattr(h, _ORCA_HANDLER_FLAG, False) for h in app_logger.handlers):
        handler = logging.StreamHandler(sys.stderr)  # same stream uvicorn uses
        handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s: %(message)s"))
        setattr(handler, _ORCA_HANDLER_FLAG, True)
        app_logger.addHandler(handler)
    for handler in app_logger.handlers:
        if getattr(handler, _ORCA_HANDLER_FLAG, False):
            handler.setLevel(level)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # _warm_llm_clients is defined further down (it needs _build_graph); this
    # only resolves it when startup actually runs.
    _apply_log_level()
    await _warm_llm_clients()
    yield


app = FastAPI(title="ORCA Gateway", version="0.1.0", lifespan=_lifespan)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_allowed_origins.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory session store — see module docstring.
_SESSIONS: dict[str, ConversationContext] = {}

# Order the graph's nodes run in (LLD §4.1 fan-out/fan-in). astream()'s
# "updates" mode yields one {node_name: partial_state} dict per completed
# node, in real completion order — for the 3 parallel specialists that's
# genuinely whichever finishes first, which is the honest thing to stream to
# the client (FR-UI-3). This tuple is just what we iterate to pull each
# update's (at most one) matching key out; it doesn't reorder anything.
_GRAPH_NODES = ("planner", "weather", "ocean", "geofencing", "risk_safety", "synthesis")


class SessionResponse(BaseModel):
    session_id: str
    created_at: str


class QueryRequest(BaseModel):
    mode: str  # "voice" | "text"
    audio_base64: str | None = None
    text: str | None = None
    location_hint: dict | None = None


class QueryResponse(BaseModel):
    text: str
    language: str
    audio_base64: str | None = None
    # None means "no verdict was requested" (#126: an informational-only
    # query, e.g. plain weather, that never invoked Risk/Safety) — distinct
    # from "INSUFFICIENT_DATA", which means Risk/Safety WAS asked and
    # couldn't reach one. Conflating the two used to show an
    # "Insufficient Data" badge on a plain weather question that never asked
    # for a safety verdict in the first place.
    verdict: str | None
    citations: list[dict]
    map_payload: dict


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Basic liveness probe — used by CI/deployment, not an SRS requirement,
    but cheap and useful for the demo-day fallback script (SRS §6.5 Sprint 4).
    (Trivial edit — verifying the CI -> deploy-backend gate end-to-end.)"""
    return {"status": "ok"}


def _get_or_create_context(session_id: str) -> ConversationContext:
    """Query endpoints auto-create a context for an unseen session_id rather
    than 404ing — a dev/test client hitting the WS or sync-query endpoint
    directly (skipping POST /api/v1/session) still works, consistent with
    the rest of this codebase's degrade-don't-block posture. GET history
    still 404s on an unknown id (see session_history) since "no history to
    show" and "this id was never created" are genuinely different there."""
    context = _SESSIONS.get(session_id)
    if context is None:
        context = ConversationContext(session_id=session_id)
        _SESSIONS[session_id] = context
    return context


def _seed_location_hint(context: ConversationContext, location_hint: dict | None) -> None:
    """Optional client-supplied location_hint (LLD §5.2, e.g. browser
    geolocation). Only used as a last-resort fallback: when the query's own
    entity extraction doesn't resolve a location AND there's no prior turn
    to fall back on either (FR-PLAN-5 already covers "no location in query
    but we've talked before this session"; this covers the very first turn).
    Recorded as a synthetic turn so ConversationContext.last_known_location()
    picks it up via the exact same path a real resolved turn would, rather
    than adding a second fallback mechanism into the Planner itself."""
    if location_hint is None or context.turns:
        return
    lat, lon = location_hint.get("lat"), location_hint.get("lon")
    if lat is None or lon is None:
        return
    context.append_turn("", {"location": {"place_name": None, "lat": lat, "lon": lon}})


async def _normalize_query(body: QueryRequest) -> NormalizedQuery:
    """Builds a NormalizedQuery from the client's "query" message (LLD §5.2).

    mode == "voice": ASR via BhashiniClient.transcribe() gives text + detected
    language together. mode == "text": language is detected separately
    (FR-LANG-2 applies to both input modes).

    BhashiniClient isn't implemented yet (#31, P2 — still raises
    NotImplementedError). Voice queries can't proceed without a transcript,
    so any failure there is surfaced as BhashiniUnavailableError for the
    caller to turn into the FR-LANG-6 user-visible message. Text queries CAN
    proceed without language detection, so that failure degrades to English
    with a logged warning instead of blocking every text query on a module
    three other people are still building — same "never crash the query"
    posture as the rest of the graph (LLD §6)."""
    if body.mode == "voice":
        try:
            if not body.audio_base64:
                raise ValueError("mode='voice' requires audio_base64")
            audio_bytes = base64.b64decode(body.audio_base64)
            transcript = await asyncio.to_thread(BhashiniClient().transcribe, audio_bytes)
            return NormalizedQuery(text=transcript.text, language=transcript.language_code)
        except Exception as exc:  # noqa: BLE001 - FR-LANG-6: degrade, don't crash the socket
            logger.warning("Speech-to-text unavailable (%s)", exc)
            raise BhashiniUnavailableError(str(exc)) from exc

    text = body.text or ""
    try:
        language = await asyncio.to_thread(BhashiniClient().detect_language, text)
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.warning("Language detection unavailable (%s) — defaulting to English", exc)
        language = "en"
    return NormalizedQuery(text=text, language=language)


async def _synthesize_audio(text: str, language: str, mode: str) -> str | None:
    """TTS for the final response, voice-mode only. A synthesis failure must
    not drop the (already-composed) text response — degrade to text-only."""
    if mode != "voice" or not text:
        return None
    try:
        audio_bytes = await asyncio.to_thread(BhashiniClient().synthesize, text, language)
        return base64.b64encode(audio_bytes).decode("ascii")
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.warning("TTS synthesis unavailable (%s) — sending text-only response", exc)
        return None


def _final_response(
    state: dict[str, Any], language: str, audio_base64: str | None
) -> QueryResponse:
    """Graph final state -> the LLD §5.2 final_response shape. state["composed"]
    is absent when the Planner asked for clarification instead of running any
    agents (graph.py's synthesis node skips the LLM call in that case, but
    still populates `composed` with the clarification prompt) — the None
    branch below is defensive (e.g. a crashed graph run) rather than the
    normal clarification path."""
    composed: ComposedResponse | None = state.get("composed")
    results = state.get("results") or {}
    risk = results.get("risk_safety")
    # #126: was Risk/Safety even asked for? graph._risk_node() only ever
    # writes the "risk_safety" key into results when the plan actually
    # invoked it (degraded/timed-out still writes the key, with value None) —
    # its absence means an informational-only query (plain weather,
    # fishing/boundary questions) never requested a verdict at all. That's
    # not the same as one being withheld, so it reports verdict=None rather
    # than "INSUFFICIENT_DATA".
    risk_requested = "risk_safety" in results
    if not risk_requested:
        verdict: str | None = None
    else:
        # Risk/Safety was requested but produced nothing usable (crashed run,
        # degraded agent) -> the same "don't claim more than we know" rule as
        # everywhere else in this system: never default to a verdict that
        # reads as SAFE.
        verdict = risk.verdict if isinstance(risk, RiskVerdict) else "INSUFFICIENT_DATA"

        # #121: same rule, applied to the case where the *explanation* is what
        # failed. Synthesis degrading to graph._unavailable_composed_response()
        # used to still render a green SAFE badge above "I couldn't put together
        # an answer" — a verdict with zero supporting reasoning, which is
        # exactly what FR-SYN-2 and SynthesisAgent's own refusal-to-ship checks
        # exist to prevent. Withholding the verdict is not fabricating one:
        # Risk's computation is unchanged, it is simply not presented as
        # actionable when we cannot say why.
        if not state.get("synthesis_ok", True):
            logger.warning(
                "Gateway: synthesis degraded — reporting INSUFFICIENT_DATA instead of "
                "the unexplained %s verdict (#121)",
                verdict,
            )
            verdict = "INSUFFICIENT_DATA"

    if composed is None:
        return QueryResponse(
            text="",
            language=language,
            audio_base64=audio_base64,
            verdict=verdict,
            citations=[],
            map_payload={"markers": [], "zones": []},
        )

    citations = [
        {"source": c.source, "timestamp": c.timestamp.isoformat()} for c in composed.citations
    ]
    map_payload = {
        "markers": composed.map_payload.markers,
        "zones": composed.map_payload.zones,
    }
    return QueryResponse(
        text=composed.text,
        language=language,
        audio_base64=audio_base64,
        verdict=verdict,
        citations=citations,
        map_payload=map_payload,
    )


def _build_graph():
    """Thin seam around build_orchestration_graph() so tests can monkeypatch
    which graph the Gateway runs (fake agents, per graph.py's own injection
    pattern) without needing real LLM/DB/external-API access — mirrors why
    build_orchestration_graph() itself takes optional agent params.

    The compiled graph is built once and reused (#149) — the "cache the
    compiled graph rather than rebuilding per request" TODO that stood here.
    It became worth the complexity when it turned out to be a correctness
    problem, not just a cost: rebuilding per request meant a fresh
    PlannerAgent/SynthesisAgent per query, hence a fresh LLM client and a
    fresh connection pool, hence a DNS + TCP + TLS handshake on every single
    query. On a cold, CPU-throttled instance the first one exceeded even the
    25s extraction deadline, so the first query after any deploy failed.

    Agents are now shared across concurrent requests. They hold only lazily
    built clients (httpx is thread-safe) and the adapters keep their state in
    module-level caches guarded by their own locks — which is the pattern
    those caches were written for. Per-query state lives in GraphState, not
    on the agents.
    """
    global _COMPILED_GRAPH
    with _COMPILED_GRAPH_LOCK:
        if _COMPILED_GRAPH is None:
            _COMPILED_GRAPH = build_orchestration_graph()
        return _COMPILED_GRAPH


def _reset_compiled_graph() -> None:
    """Test hook (see tests/conftest.py). Not used in production code."""
    global _COMPILED_GRAPH
    with _COMPILED_GRAPH_LOCK:
        _COMPILED_GRAPH = None


async def _warm_llm_clients() -> None:
    """Pay the first-call costs before a user is waiting on them (#141).

    The deployed Planner call 504'd on its 10s deadline while measuring 0.9s
    against the same API from a laptop. The difference was cold start: that
    query was the first on a 36-second-old container, and PlannerAgent /
    SynthesisAgent both import google.genai *inside* _build_llm_client(), so
    the first request pays a heavy package import plus client construction and
    a TLS handshake — on a free instance sharing one core with the rest of the
    graph. The user's query then spends its deadline on work that has nothing
    to do with the model.

    Constructing a client opens no socket, which is why #141 alone did not fix
    it (#149): the import cost moved off the user's clock but DNS + TCP + TLS
    did not, and that is the expensive half. So the warm-up now makes one real
    round trip. Combined with the shared clients in planner_agent /
    synthesis_agent, the connection it opens is the one the first query uses.

    Deliberately best-effort: warming is an optimisation, and a Gateway that
    refuses to boot because an LLM client could not be constructed is strictly
    worse than one that serves a degraded first query. Every agent below
    already degrades on its own (LLD §6).
    """
    try:
        # Building the graph constructs every agent and adapter once, and the
        # result is cached — so the first real query isn't doing it while the
        # clock runs.
        await asyncio.to_thread(_build_graph)
        await asyncio.to_thread(_open_llm_connection)
        logger.info("Gateway: LLM clients and graph warmed at startup (#141, #149)")
    except Exception as exc:  # noqa: BLE001 - warming must never block boot
        logger.warning(
            "Gateway: startup warm-up failed (%s) — the first query will pay "
            "the connection cost instead",
            exc,
        )


def _open_llm_connection() -> None:
    """One minimal generate_content, purely to open the connection (#149).

    A token of quota per container boot, against a first query that otherwise
    spends 25s on a TLS handshake and then answers "could you rephrase".
    Only the Planner's client is warmed: both agents' clients talk to the same
    host, so DNS and the TLS session are shared even though the pools are not,
    and the Planner is the call that has no fallback worth having.
    """
    client = PlannerAgent()._build_llm_client()  # the shared instance (#149)
    client.models.generate_content(
        model=planner_module._GEMINI_MODEL,
        contents="ping",
        config={"max_output_tokens": 1},
    )


@app.post("/api/v1/session", response_model=SessionResponse)
async def create_session() -> SessionResponse:
    session_id = str(uuid.uuid4())
    _SESSIONS[session_id] = ConversationContext(session_id=session_id)
    return SessionResponse(session_id=session_id, created_at=datetime.now(UTC).isoformat())


@app.websocket("/ws/v1/query/{session_id}")
async def query_socket(websocket: WebSocket, session_id: str) -> None:
    """Streams trace_update messages as each graph node completes, then one
    final_response (LLD §5.2). One query per connection, matching the LLD's
    single request/response-stream example — the client reconnects (or we
    extend this to a receive loop) for a follow-up turn."""
    await websocket.accept()
    try:
        raw = await websocket.receive_json()
    except WebSocketDisconnect:
        return
    except Exception as exc:  # noqa: BLE001 - malformed frame, not a crash
        logger.warning("query_socket: malformed frame on receive_json: %s", exc)
        await websocket.send_json({"type": "error", "message": "Malformed query message."})
        await websocket.close()
        return

    try:
        body = QueryRequest(**{k: v for k, v in raw.items() if k != "type"})
    except Exception as exc:  # noqa: BLE001 - bad payload shape, not a crash
        logger.warning("query_socket: bad QueryRequest payload shape: %s", exc)
        await websocket.send_json({"type": "error", "message": "Malformed query message."})
        await websocket.close()
        return

    context = _get_or_create_context(session_id)
    _seed_location_hint(context, body.location_hint)

    try:
        query = await _normalize_query(body)
    except BhashiniUnavailableError:
        # FR-LANG-6: user-visible error, not a silent failure / dropped connection.
        await websocket.send_json(
            {"type": "error", "message": "Language service is temporarily unavailable."}
        )
        await websocket.close()
        return

    compiled = _build_graph()
    initial_state: dict[str, Any] = {
        "query": query,
        "context": context,
        "language": query.language,
    }

    state: dict[str, Any] = {}
    try:
        async for update in compiled.astream(initial_state, stream_mode="updates"):
            for node in _GRAPH_NODES:
                payload = update.get(node)
                if not payload:
                    continue
                for key, value in payload.items():
                    if key == "trace":
                        continue
                    if key == "results":
                        state["results"] = {**state.get("results", {}), **value}
                    else:
                        state[key] = value
                for line in payload.get("trace", []):
                    await websocket.send_json({"type": "trace_update", "step": line})

        composed_text = state["composed"].text if state.get("composed") else ""
        audio_b64 = await _synthesize_audio(composed_text, query.language, body.mode)
        response = _final_response(state, query.language, audio_b64)
        await websocket.send_json({"type": "final_response", **response.model_dump()})
    except WebSocketDisconnect:
        return
    except Exception as exc:  # noqa: BLE001 - #129: every node already degrades
        # (see graph.py's _call_bounded); reaching here means an actual bug
        # in the wiring above it (main.py itself), not an agent/LLM failure.
        # That must not vanish as an unexplained dropped connection.
        logger.error("query_socket: unhandled error building the response", exc_info=exc)
        try:
            await websocket.send_json(
                {"type": "error", "message": "Something went wrong processing that query."}
            )
        except Exception:  # noqa: BLE001 - socket may already be unusable
            pass
    finally:
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001 - already closed/disconnected, nothing to do
            pass


@app.post("/api/v1/query/{session_id}", response_model=QueryResponse)
async def query_sync(session_id: str, body: QueryRequest) -> QueryResponse:
    """Non-streaming fallback for text-only clients (HLD §5.1, LLD §5.3) —
    same normalization/graph/response-building as query_socket, minus the
    incremental trace_update messages (nothing here needs them)."""
    context = _get_or_create_context(session_id)
    _seed_location_hint(context, body.location_hint)

    try:
        query = await _normalize_query(body)
    except BhashiniUnavailableError as exc:
        raise HTTPException(
            status_code=503, detail="Language service is temporarily unavailable."
        ) from exc

    compiled = _build_graph()
    initial_state: dict[str, Any] = {
        "query": query,
        "context": context,
        "language": query.language,
    }
    state = await compiled.ainvoke(initial_state)

    composed_text = state["composed"].text if state.get("composed") else ""
    audio_b64 = await _synthesize_audio(composed_text, query.language, body.mode)
    return _final_response(state, query.language, audio_b64)


@app.get("/api/v1/session/{session_id}/history")
async def session_history(session_id: str) -> list[dict]:
    context = _SESSIONS.get(session_id)
    if context is None:
        raise HTTPException(status_code=404, detail="Unknown session_id.")
    return list(context.turns)
