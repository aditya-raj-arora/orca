# Traceability Matrix — Requirement → Design → Code → Owner

This is the living version of the SRS/HLD/LLD traceability chain
(SRS §3 requirements → HLD §8 matrix → LLD §7 matrix), extended down to actual
source files so it's checkable against the real repo, not just the design docs.

Update this table in the same PR whenever a module's implementation file
changes location or a new requirement is picked up — it is meant to always
reflect reality, not to be a one-time snapshot. CI does not enforce this file
(unlike PR-level tagging, see `.github/workflows/traceability-check.yml`) —
it's kept accurate by review discipline (CONTRIBUTING.md §4).

| Requirement group | HLD component | LLD section | Source | Owner |
|---|---|---|---|---|
| FR-LANG-1..6 | Bhashini Integration Module | LLD §2.1 | `src/backend/app/language/bhashini_client.py` | P2 |
| FR-PLAN-1..5 | Planner Agent | LLD §2.2, §4.1 | `src/backend/app/orchestration/planner_agent.py` | P1 |
| FR-WX-1..4 | Weather Agent | LLD §2.3 | `src/backend/app/agents/weather_agent.py`, `data_access/weather_adapter.py` | P3 |
| FR-OCEAN-1..4 | Ocean Agent | LLD §2.4, §4.3 | `src/backend/app/agents/ocean_agent.py`, `data_access/incois_adapter.py` | P3 |
| FR-GEO-1..4 | Geofencing Agent | LLD §2.5 | `src/backend/app/agents/geofencing_agent.py`, `data_access/gis_boundary_adapter.py` | P4 |
| FR-RISK-1..3 | Risk / Safety Agent | LLD §2.6, §4.2 | `src/backend/app/agents/risk_safety_agent.py` | P4 |
| FR-SYN-1..3 | Synthesis Agent | LLD §2.7 | `src/backend/app/orchestration/synthesis_agent.py` | P2 |
| FR-UI-1..4 | Web Client | HLD §3 | `src/frontend/src/components/Chat/`, `Map/`, `TraceViewer/` | P5, P6 |
| FR-ALERT-1 | Planner + Risk/Safety extension point | HLD §3 | *not in scope for initial prototype — see SRS §6.5 Sprint 4* | — |
| NFR-PERF-1..3 | Orchestration Layer (async fan-out, timeouts) | LLD §6 | `src/backend/app/orchestration/graph.py` | P1 |
| NFR-REL-1..2 | Data Access Layer + Risk/Safety Agent | LLD §2.9, §2.6 | `data_access/*.py`, `risk_safety_agent.py` | P3, P4 |
| NFR-USE-1..3 | Web Client | HLD §3 | `src/frontend/src/components/` | P5, P6 |
| NFR-SEC-1..3 | Bhashini module, Gateway, config | LLD §2.1, §2.8 | `language/bhashini_client.py`, `main.py`, `core/config.py` | P1, P2 |
| Session / ConversationTurn / AgentInvocation / GeofenceBoundary | Persistence | LLD §3 | `src/backend/app/db/` | P4 |

## Requirement labels on GitHub issues

Every issue/PR should carry a `req:FR-XXX` or `req:NFR-XXX` label (see
CONTRIBUTING.md §7) so this matrix can eventually be cross-checked against
closed issues via the GitHub Project board, not just this static file.
