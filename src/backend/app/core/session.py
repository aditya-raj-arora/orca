"""
ConversationContext — in-memory/DB-backed multi-turn context used by the Planner
Agent to resolve follow-up queries (FR-PLAN-5, e.g. "what about tomorrow?").

Owner: P1 (Backend/Orchestration Lead).
Reference: LLD v1.0 §2.2 (PlannerAgent.plan reads/updates this), §3 (session /
conversation_turn tables it is ultimately persisted through — DB access itself
is owned by P4, see app/db/).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ConversationContext:
    session_id: str
    # Ordered list of prior (query, resolved_entities) pairs for pronoun / omitted
    # -location resolution. Kept intentionally simple (list, not a graph) for the
    # prototype scope — see SRS §2.5 constraints.
    turns: list[dict[str, Any]] = field(default_factory=list)

    def last_known_location(self) -> dict[str, Any] | None:
        """TODO(P1): implement — walk `turns` backwards for the most recent
        resolved location entity, used when a follow-up query omits location."""
        raise NotImplementedError

    def append_turn(self, query_text: str, resolved_entities: dict[str, Any]) -> None:
        """TODO(P1): implement — append and (via app/db/session_repo.py, owned by
        P4/P1 jointly) persist a conversation_turn row per LLD §3 schema."""
        raise NotImplementedError
