"""
Shared primitive types used across every agent contract.

Owner: P1 (Backend/Orchestration Lead) — this file is LOCKED after the Day 1
contract-lock sync (see CONTRIBUTING.md §6). Any change here can silently break
every other agent's contract — do not edit without opening a `contract-change`
issue and pinging all module owners same day.

Reference: LLD v1.0 §2 (dataclasses appear inline per-agent in the LLD; this file
centralises the ones shared by more than one agent so they aren't redefined
five different ways).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

AdapterStatus = Literal["ok", "unavailable", "stale"]
AgentStatus = Literal["ok", "unavailable"]


@dataclass(frozen=True)
class LatLon:
    """A WGS84 coordinate pair. All agents/adapters use this — do not pass raw
    (lat, lon) tuples across module boundaries."""

    lat: float
    lon: float


@dataclass(frozen=True)
class TimeWindow:
    """An inclusive [start, end) window, e.g. 'tomorrow morning' resolved by the
    Planner's entity extraction (LLD §2.2) into concrete UTC timestamps."""

    start: datetime
    end: datetime


@dataclass(frozen=True)
class Citation:
    """One (source, timestamp) pair backing a claim in the Synthesis Agent's
    output. Every sentence in a ComposedResponse must map to at least one of
    these — see FR-SYN-2 and the citation-coverage check in LLD §2.7."""

    source: str
    timestamp: datetime
