"""
Repository functions for session / conversation_turn / agent_invocation
persistence, used by the Gateway (session lifecycle) and the Planner
(ConversationContext persistence, FR-PLAN-5).

Owner: P1 (Backend/Orchestration Lead) for the Gateway-facing calls, P4
(Geospatial & Risk Engineer, owns app/db/) for the underlying schema — this
file is the shared seam between the two, coordinate changes.
Reference: LLD v1.0 §3.
"""
from __future__ import annotations

import uuid

from app.db.models import AgentInvocation, ConversationTurn, Session


async def create_session(language: str | None, client_metadata: dict | None) -> uuid.UUID:
    """TODO(P1): insert a Session row (app/db/models.py), return session_id.
    Backs POST /api/v1/session (LLD §5.1)."""
    raise NotImplementedError


async def append_conversation_turn(
    session_id: uuid.UUID, role: str, text: str, detected_language: str | None
) -> uuid.UUID:
    """TODO(P1): insert a ConversationTurn row. Backs FR-PLAN-5 multi-turn
    context and FR-UI-4 history display."""
    raise NotImplementedError


async def record_agent_invocation(
    turn_id: uuid.UUID,
    agent_name: str,
    input_payload: dict,
    output_payload: dict | None,
    status: str,
) -> uuid.UUID:
    """TODO(P1): insert an AgentInvocation row. Powers the agent-trace UI
    (FR-UI-3) and GET /api/v1/session/{id}/history (LLD §5.1)."""
    raise NotImplementedError


async def get_session_history(session_id: uuid.UUID) -> list[ConversationTurn]:
    """TODO(P1): backs GET /api/v1/session/{session_id}/history (HLD §5.1,
    supports FR-UI-4)."""
    raise NotImplementedError
