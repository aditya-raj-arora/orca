"""
Planner Agent — query decomposition and routing.

Owner: P1 (Backend/Orchestration Lead).
Implements: FR-PLAN-1 to FR-PLAN-5.
Reference: LLD v1.0 §2.2 (class contract) and §4.1 / Figure 1 (decomposition
decision tree — see docs/ORCA_LLD_v1.0.docx for the flowchart).

DESIGN NOTE (do not violate): routing is a DETERMINISTIC decision tree, not a
free-form LLM decision (LLD §4.1). Only entity extraction (location/time/intent)
uses the LLM. This keeps agent invocation predictable and testable — if you find
yourself asking the LLM "which agents should I call", you are implementing this
wrong; re-read LLD §4.1.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.session import ConversationContext
from app.schemas.synthesis import ExecutionPlan


@dataclass
class NormalizedQuery:
    text: str
    language: str


@dataclass
class QueryEntities:
    location: dict | None
    time_window: dict | None
    intent_keywords: list[str]
    confidence: float


class PlannerAgent:
    def plan(self, query: NormalizedQuery, context: ConversationContext) -> ExecutionPlan:
        """Core entry point. Returns the ordered list of agent invocations to
        perform for this query, plus a human-readable trace (FR-PLAN-4).

        TODO(P1):
          1. Call extract_entities() to get location/time/intent.
          2. If location is missing, try context.last_known_location() (FR-PLAN-5)
             before giving up.
          3. Apply the deterministic decision tree from LLD §4.1 / Figure 1 to
             decide which of {Weather, Ocean, Geofencing, Risk/Safety} to invoke
             and in what order (Risk/Safety always runs last, after its inputs).
          4. Populate ExecutionPlan.trace with each decision made, in plain
             English, for the agent-trace UI (FR-UI-3) and WebSocket
             trace_update messages (LLD §5.2).
          5. Update `context` with this turn (FR-PLAN-5).
        """
        raise NotImplementedError

    def extract_entities(self, query: NormalizedQuery) -> QueryEntities:
        """Uses the LLM provider (function-calling / tool-use) to extract
        structured entities from free text.

        TODO(P1, in coordination with P2 who owns the LLM/prompt layer for
        Synthesis — keep the function-calling schema consistent between the two
        LLM call sites):
          - Low-confidence extractions (see QueryEntities.confidence) must
            trigger a clarifying question rather than a guess (LLD §2.2).
          - On LLM provider timeout, fall back to simple keyword matching for
            intent classification (LLD §6 "Error Handling and Resilience"
            table) — do not let a slow LLM call hang the whole query.
        """
        raise NotImplementedError
