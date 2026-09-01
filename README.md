# ORCA — Marine EcOsystem Reasoning with Collaborative Agents

An agentic AI marine intelligence platform that gives fishermen and coastal
stakeholders evidence-backed, multilingual, conversational answers to safety
and fishing-ground questions — built by correlating live weather, oceanographic
(INCOIS), and maritime-boundary data through a set of specialist AI agents.

- **Problem Statement:** SIH26176 (ISRO), Theme: Disaster Management
- **Event:** Smart India Hackathon 2026
- **Team lead / P1 (Backend & Orchestration):** Aditya Raj Arora

## Start here

| If you want to... | Read |
|---|---|
| Understand *what* we're building and why | [`docs/ORCA_SRS_v1.1.docx`](docs/ORCA_SRS_v1.1.docx) — Software Requirements Specification |
| Understand the system architecture | [`docs/ORCA_HLD_v1.0.docx`](docs/ORCA_HLD_v1.0.docx) — High-Level Design |
| Understand exact classes, schemas, algorithms | [`docs/ORCA_LLD_v1.0.docx`](docs/ORCA_LLD_v1.0.docx) — Low-Level Design |
| See who owns what, the build schedule, tech stack | [`docs/ORCA_Chat_Summary.docx`](docs/ORCA_Chat_Summary.docx) — Role assignments & 6-day plan |
| Understand how we work (branching, PRs, traceability) | [`CONTRIBUTING.md`](CONTRIBUTING.md) |
| Know who to ping for a given file | [`CODEOWNERS`](CODEOWNERS) |

## System at a glance

```
Client (React + Leaflet)
   │  WebSocket / REST
   ▼
FastAPI Gateway ──▶ Bhashini Integration (ASR / TTS / Lang-ID)
   │
   ▼
Orchestration Layer (LangGraph)
   Planner Agent ──▶ Weather Agent ─┐
                  ├─▶ Ocean Agent    ├─▶ Risk/Safety Agent ─▶ Synthesis Agent ─▶ Client
                  └─▶ Geofencing Agent
   │
   ▼
Data Access / Adapter Layer ──▶ INCOIS, Weather provider, GIS boundary data
   │
   ▼
PostgreSQL + PostGIS (session, conversation, agent-invocation, geofence data)
```

Full detail: HLD §2–3, LLD §2.

## Repository layout

```
docs/                   SRS, HLD, LLD, and planning docs (source of truth — read before writing code)
src/backend/            Python / FastAPI backend: gateway, orchestration, all 4 agents, adapters, DB
src/frontend/           React client: chat UI, voice I/O, Leaflet map, agent-trace viewer
.github/workflows/      CI pipelines (backend, frontend, integration)
.github/ISSUE_TEMPLATE/ Templates for user stories and bugs — always link a requirement ID
scripts/                Dev/setup scripts
```

## Team & ownership (see CODEOWNERS for enforcement)

| # | Role | Owns | Person |
|---|---|---|---|
| P1 | Backend / Orchestration Lead | FastAPI Gateway, Planner Agent, session lifecycle, LangGraph wiring | Aditya Raj Arora ([@aditya-raj-arora](https://github.com/aditya-raj-arora)) |
| P2 | LLM / Synthesis Engineer | Synthesis Agent, entity-extraction prompts, citation check | [@Deep-R28](https://github.com/Deep-R28) |
| P3 | Weather & Ocean Data Engineer | Weather Agent, Ocean Agent, their adapters | Swaraj Rane ([@ArmouredOre](https://github.com/ArmouredOre)) |
| P4 | Geospatial & Risk Engineer | Geofencing Agent, Risk/Safety Agent, DB schema | [@m123ukta](https://github.com/m123ukta) *(invite pending)* |
| P5 | Frontend Engineer (Core UI) | Chat UI, voice I/O, Bhashini client integration | [@armoredglock](https://github.com/armoredglock) |
| P6 | Frontend Engineer (Map/Trace) + QA/Integration Lead | Map panel, trace viewer, E2E tests, deployment | *yet to join — fill in* |

Update this table and `CODEOWNERS` with real GitHub handles as teammates join — see
[`CONTRIBUTING.md`](CONTRIBUTING.md#onboarding-a-new-teammate).

## Getting started

```bash
# Backend
cd src/backend
cp .env.example .env   # fill in API keys — see docs/ORCA_SRS_v1.1.docx §2.6 for what you need
pip install -r requirements.txt
uvicorn app.main:app --reload

# Frontend
cd src/frontend
npm install
npm run dev
```

Or bring up everything (backend + Postgres/PostGIS) with:

```bash
docker compose up --build
```

## Status

🚧 Prototype build in progress — see the [Sprint plan](docs/ORCA_SRS_v1.1.docx) (SRS §6.5) and the
[6-day schedule](docs/ORCA_Chat_Summary.docx) for current sprint scope and exit criteria.
