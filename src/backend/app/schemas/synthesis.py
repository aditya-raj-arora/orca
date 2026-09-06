"""
ExecutionPlan / ComposedResponse / MapPayload contracts.

ExecutionPlan owner: P1. ComposedResponse/MapPayload owner: P2 (LLM/Synthesis
Engineer), consumed by P5/P6 (frontend) and P1 (WebSocket streaming).

Reference: LLD v1.0 §2.2 (ExecutionPlan), §2.7 (ComposedResponse), §5.2
(map_payload shape as sent over WebSocket).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.schemas.common import Citation


@dataclass
class AgentInvocationRequest:
    agent_name: str
    input_payload: dict[str, Any]


@dataclass
class ExecutionPlan:
    invocations: list[AgentInvocationRequest] = field(default_factory=list)
    # Human-readable decisions, for FR-PLAN-4 / the agent-trace UI (FR-UI-3).
    trace: list[str] = field(default_factory=list)
    # Contract addition (LLD Fig.1 "Ask clarifying follow-up for location" /
    # low-confidence-extraction path, LLD §2.2): when true, invocations is
    # always empty and the Gateway should send clarification_prompt back to
    # the user as the turn's response instead of running any agents.
    needs_clarification: bool = False
    clarification_prompt: str | None = None


@dataclass
class MapPayload:
    # TODO(P6 - Map/Trace + QA/Integration Lead): finalise marker/zone schema
    # against what Leaflet needs (HLD §6 "Leaflet ... marker/zone visualisation").
    # Keep this in sync with the "map_payload" shape in LLD §5.2's
    # final_response example message.
    markers: list[dict[str, Any]] = field(default_factory=list)
    zones: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ComposedResponse:
    text: str
    citations: list[Citation] = field(default_factory=list)
    map_payload: MapPayload = field(default_factory=MapPayload)
    trace: list[str] = field(default_factory=list)
    # False when this text is a degraded stand-in rather than a composed,
    # citation-checked answer (#133). The Gateway must not render a verdict
    # badge above an unverified response — that is #121's rule, which until now
    # was enforced by comparing the response against a sentinel OBJECT in
    # graph.py. That identity check could only see the one degradation the
    # graph itself built, so SynthesisAgent's own _degraded_response() slipped
    # past it and could put a green SAFE badge over text saying it had failed
    # to verify anything. Additive with a safe default: an omitted flag means
    # "verified", so every existing construction keeps its meaning.
    verified: bool = True
