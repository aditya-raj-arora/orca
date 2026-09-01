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
    #
    # This in-memory list is a per-request cache the Gateway rehydrates from the
    # conversation_turn table (app/db/session_repo.py::get_session_history) at
    # the start of each turn; append_turn() below does NOT itself write to the
    # DB — that's a separate persistence step the Gateway performs alongside
    # this (LLD §3 record_agent_invocation / append_conversation_turn), so a
    # unit test can exercise ConversationContext with zero DB dependency.
    turns: list[dict[str, Any]] = field(default_factory=list)

    def last_known_location(self) -> dict[str, Any] | None:
        """Walk `turns` backwards for the most recent resolved location entity,
        used when a follow-up query omits location (FR-PLAN-5, LLD Fig.1's
        "Location resolvable?" check falls back to this before asking a
        clarifying question)."""
        for turn in reversed(self.turns):
            location = turn.get("location")
            if location:
                return location
        return None

    def append_turn(self, query_text: str, resolved_entities: dict[str, Any]) -> None:
        """Append this turn's resolved entities to the in-memory context so a
        subsequent follow-up in the same session can resolve against it.

        `resolved_entities` is expected to at least carry a "location" key
        (possibly None, if this turn didn't resolve one) — see
        PlannerAgent.plan() in orchestration/planner_agent.py, the only
        caller. Separate from — and does not perform — the durable DB write;
        see the class docstring above.
        """
        self.turns.append({"query_text": query_text, **resolved_entities})
