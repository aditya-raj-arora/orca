#!/usr/bin/env python3
"""
e2e_live_check.py — run real queries through the whole ORCA pipeline against
live external services and print a readable transcript.

Owner: P1 (Backend/Orchestration Lead). Issue #36.

This is the human-readable companion to tests/integration/test_live_e2e.py:
the test file answers "did it pass"; this answers "what did it actually say,
which agents ran, and how long did each stage take" — which is what you want
when tuning prompts (#43) or rehearsing the demo (#47).

No mocks anywhere: real Gemini, real Open-Meteo / WeatherAPI / GDACS, real
INCOIS GeoServer, real bundled boundary data, real LangGraph run.

Usage:

    cd src/backend
    LLM_API_KEY=...  python ../../scripts/e2e_live_check.py
    LLM_API_KEY=...  python ../../scripts/e2e_live_check.py "your own query"

Exit code is 0 only if every scenario produced a cited answer inside the
NFR-PERF-2 budget, so this is also usable as a pre-demo smoke gate.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / "src" / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.agents.geofencing_agent import GeofencingAgent  # noqa: E402
from app.agents.ocean_agent import OceanAgent  # noqa: E402
from app.agents.risk_safety_agent import RiskSafetyAgent  # noqa: E402
from app.agents.weather_agent import WeatherAgent  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.session import ConversationContext  # noqa: E402
from app.data_access.gis_boundary_adapter import GISBoundaryAdapter  # noqa: E402
from app.data_access.incois_adapter import INCOISAdapter  # noqa: E402
from app.data_access.weather_adapter import WeatherDataAdapter  # noqa: E402
from app.orchestration.graph import run_query  # noqa: E402
from app.orchestration.planner_agent import NormalizedQuery, PlannerAgent  # noqa: E402
from app.orchestration.synthesis_agent import SynthesisAgent  # noqa: E402

NFR_PERF_2_BUDGET_S = 15.0

# Chosen to exercise different branches of Figure 1, not just to vary wording:
# a multi-intent fan-out, a safety-only single-agent query, and a boundary
# query — the three routing shapes the Planner can produce for a resolvable
# location.
SCENARIOS: list[tuple[str, str]] = [
    (
        "multi-agent (safety + fishing + boundary)",
        "Is it safe to go fishing near Kochi today, and am I close to any "
        "restricted or protected zone?",
    ),
    ("safety only", "What are the sea conditions off Chennai right now?"),
    ("boundary only", "Am I anywhere near the maritime boundary if I sail from Kollam?"),
]


def _pipeline() -> dict:
    """Every component real and default-constructed. Spelled out rather than
    relying on build_orchestration_graph()'s defaults so it stays obvious that
    nothing here is faked."""
    return {
        "planner": PlannerAgent(),
        "weather_agent": WeatherAgent(WeatherDataAdapter()),
        "ocean_agent": OceanAgent(INCOISAdapter()),
        "geofencing_agent": GeofencingAgent(GISBoundaryAdapter()),
        "risk_agent": RiskSafetyAgent(),
        "synthesis_agent": SynthesisAgent(),
    }


def _rule(char: str = "-") -> str:
    return char * 78


async def _run_one(label: str, query: str) -> bool:
    print(f"\n{_rule('=')}\n{label}\n  query: {query}\n{_rule('=')}")

    started = time.perf_counter()
    state = await run_query(
        NormalizedQuery(text=query, language="en"),
        ConversationContext(session_id=f"e2e-{label}"),
        **_pipeline(),
    )
    elapsed = time.perf_counter() - started

    print("\nTRACE")
    for line in state.get("trace", []):
        print(f"  - {line}")

    results = state.get("results") or {}
    print("\nAGENT RESULTS")
    for name in sorted(results):
        print(f"  {name:<14} {results[name]!r}")

    composed = state.get("composed")
    risk = results.get("risk_safety")
    verdict = getattr(risk, "verdict", "(none — informational query)")

    print(f"\nVERDICT   {verdict}")
    print("\nANSWER")
    print(f"  {composed.text if composed else '(none)'}")

    citations = list(composed.citations) if composed else []
    print("\nCITATIONS")
    for citation in citations:
        print(f"  - {citation.source} @ {citation.timestamp.isoformat()}")
    if not citations:
        print("  (none)")

    # A clarifying follow-up is a legitimate outcome, not a failure — but it is
    # not an answer, so it is not held to the citation requirement.
    clarified = state.get("plan") is not None and state["plan"].needs_clarification
    within_budget = elapsed < NFR_PERF_2_BUDGET_S
    answered = bool(composed and composed.text.strip())
    cited = clarified or bool(citations)

    print(f"\nELAPSED   {elapsed:.2f}s (NFR-PERF-2 budget {NFR_PERF_2_BUDGET_S}s)")
    ok = answered and cited and within_budget
    print(f"RESULT    {'PASS' if ok else 'FAIL'}")
    if not answered:
        print("          ! no answer text was produced")
    if not cited:
        print("          ! answer shipped with no citations (FR-SYN-2)")
    if not within_budget:
        print("          ! over the NFR-PERF-2 budget")
    return ok


async def main() -> int:
    if not get_settings().llm_api_key:
        print(
            "LLM_API_KEY is not set — the Planner and Synthesis agents both need it.\n"
            "Set it in src/backend/.env or the environment (see docs/CREDENTIALS.md #1).",
            file=sys.stderr,
        )
        return 2

    scenarios = (
        [("custom", " ".join(sys.argv[1:]))] if len(sys.argv) > 1 else SCENARIOS
    )

    outcomes = [await _run_one(label, query) for label, query in scenarios]

    print(f"\n{_rule('=')}")
    print(f"SUMMARY   {sum(outcomes)}/{len(outcomes)} scenarios passed")
    print(_rule("="))
    return 0 if all(outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
