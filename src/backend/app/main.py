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
"""
from __future__ import annotations

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.core.config import get_settings
from app.language.bhashini_client import BhashiniUnavailableError

app = FastAPI(title="ORCA Gateway", version="0.1.0")

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_allowed_origins.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)


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
    verdict: str
    citations: list[dict]
    map_payload: dict


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Basic liveness probe — used by CI/deployment, not an SRS requirement,
    but cheap and useful for the demo-day fallback script (SRS §6.5 Sprint 4)."""
    return {"status": "ok"}


@app.post("/api/v1/session", response_model=SessionResponse)
async def create_session() -> SessionResponse:
    """TODO(P1): call app.db.session_repo.create_session(), return the real
    session_id + created_at (LLD §5.1)."""
    raise NotImplementedError


@app.websocket("/ws/v1/query/{session_id}")
async def query_socket(websocket: WebSocket, session_id: str) -> None:
    """Streams ASR partials, agent trace, and the final composed response.

    TODO(P1):
      1. Accept the connection, receive the "query" message (LLD §5.2 schema).
      2. If mode == "voice", pass audio_base64 through
         app.language.bhashini_client.BhashiniClient.transcribe(); catch
         BhashiniUnavailableError and send a clear user-visible error per
         FR-LANG-6 (see the except clause stub below) instead of dropping the
         connection silently.
      3. Hand the normalized text query into the orchestration graph
         (app.orchestration.graph.build_orchestration_graph()).
      4. Stream a `trace_update` message (LLD §5.2) at each orchestration step.
      5. On completion, synthesize TTS audio (if mode == voice) and send the
         `final_response` message (LLD §5.2 schema) — text, citations, verdict,
         map_payload, trace.
      6. Persist the turn + agent invocations via app.db.session_repo.
    """
    await websocket.accept()
    try:
        raise NotImplementedError
    except BhashiniUnavailableError:
        # FR-LANG-6: user-visible error, not a silent failure.
        await websocket.send_json(
            {"type": "error", "message": "Language service is temporarily unavailable."}
        )


@app.post("/api/v1/query/{session_id}", response_model=QueryResponse)
async def query_sync(session_id: str, body: QueryRequest) -> QueryResponse:
    """Non-streaming fallback for text-only clients (HLD §5.1). Same schema as
    the WebSocket "query" request / "final_response" response.

    TODO(P1): implement — shares logic with query_socket() minus the
    incremental trace streaming; consider extracting a shared
    `run_query(session_id, body) -> QueryResponse` helper so the two endpoints
    don't diverge in behaviour.
    """
    raise NotImplementedError


@app.get("/api/v1/session/{session_id}/history")
async def session_history(session_id: str) -> list[dict]:
    """TODO(P1): call app.db.session_repo.get_session_history(), serialize to
    plain dicts for the frontend (supports FR-UI-4)."""
    raise NotImplementedError
