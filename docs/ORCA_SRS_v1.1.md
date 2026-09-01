> **Readability mirror.** This file is generated from [`ORCA_SRS_v1.1.docx`](ORCA_SRS_v1.1.docx), which remains the authoritative, sign-off-bearing source document (SIH submission format). If this file and the .docx ever diverge, the .docx wins — regenerate this file after any .docx revision rather than hand-editing it out of sync.

---

# Software Requirements Specification — ORCA

**Marine EcOsystem Reasoning with Collaborative Agents**
*An Agentic AI Marine Intelligence Platform*

| | |
|---|---|
| Problem Statement ID | SIH26176 |
| Organization | Indian Space Research Organisation (ISRO) |
| Theme | Disaster Management |
| Document Version | 1.1 |
| Prepared for | Smart India Hackathon 2026 |
| Prepared by | Aditya Raj Arora |

## Document Control

### Revision History

| Version | Date | Author | Description of Change |
|---|---|---|---|
| 0.1 | Draft | Aditya Raj Arora | Initial draft for internal review |
| 1.0 | Baseline | Aditya Raj Arora | Baseline SRS for Sprint 0 sign-off (36-hour hackathon build window assumption) |
| 1.1 | 29 Aug 2026 | Aditya Raj Arora | Development timeline revised from a 36-hour hackathon build window to a 9-day initial prototype build phase (29 Aug – 7 Sept 2026). Section 2.5, 6.1, and 6.2 updated accordingly; risk register in Section 6.3 re-assessed against the longer window. |

### Approval

This document is baselined at the end of Sprint 0 and is expected to evolve across sprints under the Agile development model adopted for this project. Material changes to scope must be reflected here with a version increment.

## Table of Contents

(Right-click below and select "Update Field" in Microsoft Word to populate the table of contents.)

## 1. Introduction

### 1.1 Purpose

This Software Requirements Specification (SRS) defines the functional and non-functional requirements for ORCA (Marine EcOsystem Reasoning with Collaborative Agents), an agentic AI-powered conversational platform that provides marine stakeholders — principally fishermen — with intelligent, evidence-based decision support drawn from satellite Earth Observation data, oceanographic advisories, and weather forecasts. This document is intended for the development team, project mentor(s), and Smart India Hackathon evaluators as a baseline reference for what the system will and will not do in its current scope.

### 1.2 Scope

ORCA is a software-only (Category: Software) solution developed against Problem Statement SIH26176, sponsored by the Indian Space Research Organisation (ISRO), under the Disaster Management theme. The system will:

- Accept natural-language queries from users, by voice or text, in any of the officially supported Indian languages via Bhashini.
- Autonomously decompose a query and route it to one or more specialised AI agents (Weather, Ocean, Geofencing, Risk/Safety).
- Retrieve and correlate data from public marine, meteorological, and geospatial sources.
- Synthesise agent outputs into a single, explainable, evidence-backed conversational response, supplemented with map-based visualisation.
- Proactively surface safety alerts (adverse weather, high waves, lightning, cyclone, geofencing violations) for a queried location and time window.
Out of scope for the current version: ingestion or reprocessing of raw satellite imagery (the system consumes already-processed public advisories and bulletins, not Level-0/1 satellite products); mobile native applications (the MVP targets a responsive web client); and any functionality requiring paid/licensed data subscriptions.

### 1.3 Definitions, Acronyms, and Abbreviations

| Term | Definition |
|---|---|
| Agentic AI | An AI system architecture in which autonomous sub-agents plan, select tools, and execute tasks, coordinated by a higher-level orchestrator, rather than a single monolithic model call. |
| PFZ | Potential Fishing Zone — an advisory, published by INCOIS, indicating likely productive fishing areas based on oceanographic parameters. |
| INCOIS | Indian National Centre for Ocean Information Services. |
| IMBL | International Maritime Boundary Line. |
| MPA | Marine Protected Area. |
| Bhashini | India's national Digital Public Infrastructure for multilingual language technology (ASR, TTS, translation), under MeitY. |
| SST | Sea Surface Temperature. |
| ASR / TTS | Automatic Speech Recognition / Text-to-Speech. |
| LLM | Large Language Model. |
| SRS | Software Requirements Specification (this document). |

### 1.4 References

- Smart India Hackathon 2026 — Problem Statement SIH26176 (ORCA), sih.gov.in.
- INCOIS Potential Fishing Zone advisory service — incois.gov.in.
- Bhashini National Language Translation Mission API documentation — bhashini.gov.in.
- IEEE Std 830-1998 — Recommended Practice for Software Requirements Specifications (structural reference for this document).
### 1.5 Overview of this Document

Section 2 describes the product at a high level, its intended users, and the environment it operates in. Section 3 enumerates functional requirements grouped by system feature/agent. Section 4 specifies external interfaces. Section 5 specifies non-functional requirements. Section 6 records assumptions, dependencies, and known risks accepted for this baseline.

## 2. Overall Description

### 2.1 Product Perspective

ORCA is a new, standalone conversational platform. It is not a replacement for INCOIS's or IMD's existing publication systems; it is an intelligence and accessibility layer that sits atop already-public data sources, making them queryable in natural, spoken, multilingual form rather than requiring users to interpret static bulletins, portals, or dashboards. No existing product performs this specific agentic, conversational correlation across marine, weather, and geospatial data for the Indian context; this is confirmed as an open problem (no direct existing solution) as of the current problem statement cycle.

### 2.2 Product Functions (Summary)

- Multilingual conversational query intake (voice and text).
- Autonomous query decomposition and multi-agent task routing.
- Weather and marine-condition retrieval and interpretation.
- Potential Fishing Zone retrieval and proximity computation.
- Maritime boundary and protected-area geofencing checks.
- Composite risk/safety verdict generation.
- Explainable, evidence-cited conversational responses with supporting maps and visualisations.
- Multi-turn, context-aware conversation (follow-up queries without re-stating context).
### 2.3 User Classes and Characteristics

| User Class | Characteristics | Primary Needs |
|---|---|---|
| Fishermen / fishing crews (primary user) | Variable literacy and digital exposure; primarily feature phones or basic smartphones; regional-language speakers | Fast, simple, spoken answers to safety and location queries |
| Coastal / district disaster management authorities | Moderate-to-high digital literacy; monitor multiple locations | Aggregated alerts and situational dashboards |
| Maritime / vessel operators | Higher digital literacy; navigation-focused | Route safety, boundary proximity, weather-linked routing |
| Fisheries researchers / scientists | High technical literacy | Ad hoc, exploratory correlation queries across datasets |
| System administrators (project team) | Technical | Monitoring, data-source health, configuration |

### 2.4 Operating Environment

- Client: Responsive web application, accessible via modern mobile and desktop browsers.
- Server: Cloud-hosted or on-prem Linux environment running the FastAPI backend and agent orchestration layer.
- Network: Designed for functioning over low-bandwidth mobile data connections typical of coastal/rural areas; must degrade gracefully rather than fail outright under high latency.
### 2.5 Design and Implementation Constraints

- All data sources used must be public and freely accessible (no paid/licensed subscriptions in this version).
- Language support is bound by Bhashini's currently supported language set and its API availability/uptime.
- The initial prototype build phase runs from 29 August 2026 to 7 September 2026 (9 days). This constrains the depth of agent logic that can be implemented, tested, and hardened before the first demonstrable milestone; the Agile sprint plan (Section 6.5) sequences scope accordingly. This is understood as the prototype-readiness deadline, not necessarily the final SIH submission/demo date.
- The system depends on third-party data providers (INCOIS, meteorological APIs) whose availability, format, and rate limits are outside this project's control.
### 2.6 Assumptions and Dependencies

- INCOIS PFZ advisories and marine weather bulletins remain accessible via public web endpoints for the duration of development and demonstration.
- A Bhashini API key/sandbox access is obtained and remains available through development and demo.
- An LLM API (for the Planner and Synthesis agents) is available with sufficient rate limits for development and live demonstration.
- Geofencing boundary data (IMBL, MPA boundaries) is obtainable in a usable public GIS format.
## 3. System Features (Functional Requirements)

Requirements are grouped by system feature. Priority follows MoSCoW convention: Must Have, Should Have, Could Have, Won't Have (this version). Per the confirmed project scope, all four specialist agents and full Bhashini multilingual support are designated Must Have; a small number of resilience-related items are flagged Should Have to record known risk given the live-data-only strategy adopted for this version (see Section 6.3).

### 3.1 Language Intake and Output (Bhashini Layer)

Handles conversion between spoken/typed natural language in any Bhashini-supported Indian language and the internal query representation used by the Planner Agent.

| Req ID | Requirement | Priority |
|---|---|---|
| FR-LANG-1 | The system shall accept user input as either typed text or recorded/streamed speech. | Must |
| FR-LANG-2 | The system shall automatically identify the language of the incoming query without requiring the user to manually select a language. | Must |
| FR-LANG-3 | The system shall support all Indian languages currently available through the Bhashini API for both ASR and TTS. | Must |
| FR-LANG-4 | The system shall generate its response in the same language as the incoming query. | Must |
| FR-LANG-5 | The system shall convert the final text response to speech (TTS) for voice-mode interactions. | Must |
| FR-LANG-6 | The system shall display a clear, user-visible error message if the detected language is not currently supported by Bhashini, rather than failing silently. | Should |

### 3.2 Planner Agent (Query Decomposition and Orchestration)

The Planner Agent interprets user intent and determines which specialist agent(s) must be invoked, in what sequence, to answer the query.

| Req ID | Requirement | Priority |
|---|---|---|
| FR-PLAN-1 | The system shall decompose a natural-language query into one or more structured sub-tasks addressable by a specialist agent. | Must |
| FR-PLAN-2 | The system shall determine, for each sub-task, which specialist agent(s) (Weather, Ocean, Geofencing, Risk/Safety) are required. | Must |
| FR-PLAN-3 | The system shall support queries requiring outputs from more than one specialist agent to be combined before a final answer is produced. | Must |
| FR-PLAN-4 | The system shall expose a human-readable trace of its planning and delegation decisions, for display in the user interface. | Must |
| FR-PLAN-5 | The system shall retain conversation context across turns, allowing follow-up queries (e.g., "what about tomorrow?") to be interpreted relative to the prior query. | Must |

### 3.3 Weather Agent

Retrieves and interprets weather and sea-state conditions relevant to a queried location and time window.

| Req ID | Requirement | Priority |
|---|---|---|
| FR-WX-1 | The system shall retrieve current and short-range forecast weather data (wind, wave height, precipitation, visibility) for a specified coastal location. | Must |
| FR-WX-2 | The system shall retrieve active weather alerts (cyclone, lightning, high-wave warnings) for a specified location and time window. | Must |
| FR-WX-3 | The system shall timestamp all weather data returned, and this timestamp shall be surfaced to the end user. | Must |
| FR-WX-4 | The system shall handle an unavailable or erroring weather data source by returning a clearly flagged "data unavailable" state rather than a fabricated value. | Must |

### 3.4 Ocean Agent (Potential Fishing Zone / Oceanographic Data)

Retrieves oceanographic advisories, including Potential Fishing Zone locations, relative to a queried location.

| Req ID | Requirement | Priority |
|---|---|---|
| FR-OCEAN-1 | The system shall retrieve the nearest currently active Potential Fishing Zone(s) relative to a specified or inferred location. | Must |
| FR-OCEAN-2 | The system shall retrieve available Sea Surface Temperature and chlorophyll concentration data for a queried region where published by INCOIS. | Must |
| FR-OCEAN-3 | The system shall compute and report distance/direction from a specified location to the nearest relevant PFZ. | Must |
| FR-OCEAN-4 | The system shall flag PFZ or oceanographic data older than a configurable staleness threshold as potentially outdated. | Should |

### 3.5 Geofencing Agent

Checks a queried or inferred location against sensitive maritime boundaries and protected zones.

| Req ID | Requirement | Priority |
|---|---|---|
| FR-GEO-1 | The system shall determine whether a specified location falls within a configurable proximity threshold of the International Maritime Boundary Line. | Must |
| FR-GEO-2 | The system shall determine whether a specified location falls within a Marine Protected Area or other geofenced restricted zone. | Must |
| FR-GEO-3 | The system shall generate a distinct, unambiguous alert when a boundary or restricted-zone proximity condition is met. | Must |
| FR-GEO-4 | The system shall never suppress or downgrade a geofencing alert on the basis of other agents' outputs (e.g., a favourable weather report must not hide a boundary warning). | Must |

### 3.6 Risk / Safety Agent

Combines outputs from other agents into a single, explainable safety verdict.

| Req ID | Requirement | Priority |
|---|---|---|
| FR-RISK-1 | The system shall generate a composite safety verdict (e.g., Safe / Caution / Unsafe) for a specified location and time window, based on combined weather, oceanographic, and geofencing inputs. | Must |
| FR-RISK-2 | The system shall provide a human-readable explanation of which factor(s) drove the verdict. | Must |
| FR-RISK-3 | The system shall default to the most conservative (safety-first) verdict when any single contributing agent reports missing or unavailable data. | Must |

### 3.7 Synthesis / Reasoning Agent

Combines the outputs of all invoked agents into one coherent conversational answer.

| Req ID | Requirement | Priority |
|---|---|---|
| FR-SYN-1 | The system shall combine outputs from all agents invoked for a given query into a single, non-contradictory natural-language response. | Must |
| FR-SYN-2 | The system shall cite which underlying data source(s) and timestamp(s) support each part of its response. | Must |
| FR-SYN-3 | The system shall present supporting evidence visually (map markers, weather icons, verdict banner) alongside the conversational text response. | Must |

### 3.8 User Interface and Visualisation

The presentation layer through which users interact with ORCA.

| Req ID | Requirement | Priority |
|---|---|---|
| FR-UI-1 | The system shall provide a conversational chat-style interface supporting both voice and text input/output. | Must |
| FR-UI-2 | The system shall display an interactive map showing the queried location, relevant PFZ zone(s), and any geofencing boundaries relevant to the response. | Must |
| FR-UI-3 | The system shall visually display the agent orchestration trace (which agents were invoked and in what order) for transparency. | Must |
| FR-UI-4 | The system shall support multi-turn conversation history visible to the user within a session. | Should |

### 3.9 Proactive Alerting

Alerts issued for a location without requiring an explicit user query, referenced in the source problem statement.

| Req ID | Requirement | Priority |
|---|---|---|
| FR-ALERT-1 | The system shall support generation of a safety/hazard alert for a saved or specified location without requiring a new explicit query, where the underlying data indicates a materially changed risk condition. | Could |

## 4. External Interface Requirements

### 4.1 User Interfaces

- Web-based responsive client (desktop and mobile browser), conversational chat layout with an integrated map panel.
- Voice input via microphone capture in-browser; voice output via in-browser audio playback of TTS responses.
### 4.2 Hardware Interfaces

- None beyond standard client-device microphone/speaker and network connectivity; no proprietary or dedicated hardware is required.
### 4.3 Software Interfaces

| External System | Purpose | Interface Type |
|---|---|---|
| INCOIS Potential Fishing Zone service | PFZ advisory and oceanographic data (SST, chlorophyll) | Public API / structured web data |
| Meteorological data provider (IMD public bulletins / OpenWeather Marine API) | Weather forecasts, marine conditions, active alerts | Public REST API |
| Bhashini API | ASR, TTS, language identification | REST API (authenticated) |
| Public GIS boundary data (IMBL, MPA) | Geofencing boundary geometry | Static GIS layer / public dataset |
| LLM provider API | Planner and Synthesis agent reasoning | REST API (authenticated) |

### 4.4 Communication Interfaces

- HTTPS for all client-server and server-to-external-API communication.
- WebSocket connection between client and server for streaming conversational responses and live agent-trace updates.
## 5. Non-Functional Requirements

### 5.1 Performance

| Req ID | Requirement |
|---|---|
| NFR-PERF-1 | For a single-agent query, the system shall return a response within 8 seconds under normal external API conditions. |
| NFR-PERF-2 | For a multi-agent query (2 or more agents invoked), the system shall return a response within 15 seconds under normal external API conditions. |
| NFR-PERF-3 | The user interface shall display a visible progress/trace indicator whenever a response is expected to take longer than 3 seconds. |

### 5.2 Reliability and Availability

| Req ID | Requirement |
|---|---|
| NFR-REL-1 | The system shall clearly indicate to the user, per data category, when an external data source could not be reached, rather than silently omitting or fabricating information. |
| NFR-REL-2 | Given the adopted live-API-only data strategy (Section 6.3), the system shall not present a safety verdict as "Safe" when any safety-relevant agent could not be reached; it shall instead report an explicit "Insufficient data" state. |

### 5.3 Usability

| Req ID | Requirement |
|---|---|
| NFR-USE-1 | The system shall be operable by a first-time user with no prior training, relying on conversational prompts rather than form-based input. |
| NFR-USE-2 | The system shall not require the user to read or type in English at any point in the core query flow. |
| NFR-USE-3 | Map and visual elements shall include a text/voice-equivalent description of their content for accessibility. |

### 5.4 Security and Privacy

| Req ID | Requirement |
|---|---|
| NFR-SEC-1 | The system shall not persist voice recordings beyond the duration required to complete ASR transcription for the active session. |
| NFR-SEC-2 | All API keys and credentials for third-party services (Bhashini, LLM provider) shall be stored as environment-level secrets, never in source control. |
| NFR-SEC-3 | Query location data shall not be shared with any third party beyond the minimum required for the specific data lookup (e.g., coordinates sent only to the relevant weather/ocean API). |

### 5.5 Scalability

The MVP is scoped for demonstration-scale concurrent usage (single-digit to low tens of simultaneous sessions). The agent-based architecture is designed such that individual agents can be scaled or replaced independently in a future phase without redesigning the Planner/Synthesis orchestration layer.

### 5.6 Maintainability

Each specialist agent shall be implemented as an independently testable module with a defined input/output contract, such that a data-source change (e.g., a new weather provider) requires modification to a single agent module rather than the orchestration logic.

## 6. Assumptions, Dependencies, and Known Risks

### 6.1 Scope Baseline (as confirmed for this version)

- All four specialist agents (Weather, Ocean, Geofencing, Risk/Safety) are in scope for the MVP, not deferred.
- Full Bhashini multilingual support (not a single demo language) is a core requirement of this version.
- Data retrieval strategy is live API/scrape calls only, with no cached-snapshot fallback in this version.
- Development timeline: initial prototype to be ready by 7 September 2026, giving a 9-day build window from the date of this revision (29 August 2026).
### 6.2 Rationale for Recording This as a Risk Item

The scope confirmed in Section 6.1 was originally assessed against a 36-hour hackathon build window, where it represented a high-risk combination (Revision 1.0 of this document). With the build window revised to 9 days, overall delivery risk is substantially reduced — there is realistic time to implement, integration-test, and harden each agent in sequence rather than in parallel under time pressure. The risk items below are retained, in adjusted form, because a 9-day window is still compressed for four fully-featured agents plus full multilingual support; they should be treated as things to actively sequence and de-risk early, not as blockers.

### 6.3 Key Risks

| Risk ID | Description | Potential Impact | Suggested Mitigation (for design/sprint planning) |
|---|---|---|---|
| RISK-1 | Live-only data strategy means any INCOIS/weather API outage during development or live demonstration directly breaks the corresponding agent. | Demo failure; NFR-REL-1/2 partially unmet at runtime. | Design each agent's data-fetch layer with a swappable fallback interface now, even if the fallback itself is only wired in later. With 9 days available, allocate explicit integration-testing time (Section 6.5, Sprint 2) to observe real-world API reliability before the final days. |
| RISK-2 | Full Bhashini multilingual support across all flows increases integration surface (ASR + TTS + language ID for every supported language). | Reduced time available for agent logic and testing if all languages are validated simultaneously. | Validate the Bhashini integration pattern against 2-3 languages first (by Sprint 1), then expand to the full supported set in Sprint 3 once the core pipeline is proven — the 9-day window makes this staged approach realistic. |
| RISK-3 | All four agents fully implemented increases integration risk between Planner, Synthesis, and each specialist agent. | Orchestration bugs surfacing late, close to the prototype deadline. | Sequence agent implementation (Section 6.5) so each agent is independently testable before orchestration integration; reserve the final 1-2 days purely for integration and hardening, not new feature work. |
| RISK-4 | Public GIS boundary data (IMBL, MPA) format/availability has not yet been finalised. | Geofencing Agent may be blocked pending data-source confirmation. | Confirm and download a working boundary dataset on Day 1, before agent development begins. |

### 6.4 Dependencies Requiring Early Confirmation

- Bhashini API access credentials obtained and tested.
- LLM provider API access and rate limits confirmed sufficient for expected demo-day query volume.
- INCOIS PFZ data access method (API vs. scraping) confirmed and a sample successfully retrieved.
- IMBL/MPA boundary GIS data source identified and downloaded.
### 6.5 Revised Development Timeline (9-Day Prototype Build)

The Agile sprint structure below maps the confirmed scope (Section 6.1) onto the revised 29 August – 7 September 2026 window. Each sprint ends with a working, demonstrable increment rather than partially-built features across all agents simultaneously.

| Sprint | Days | Focus | Exit Criteria |
|---|---|---|---|
| Sprint 0 | Day 1 (29 Aug) | Finalise SRS (this document); confirm all dependencies in Section 6.4; scaffold repository and agent interfaces. | All external data sources confirmed reachable with a sample successful call; project skeleton committed. |
| Sprint 1 | Days 2–4 (30 Aug – 1 Sept) | Implement Weather Agent and Ocean Agent as independently testable modules against FR-WX and FR-OCEAN. | Both agents return correct structured data for at least 3 test locations, independent of the Planner. |
| Sprint 2 | Days 5–6 (2–3 Sept) | Implement Geofencing Agent and Risk/Safety Agent (FR-GEO, FR-RISK); begin Planner/Synthesis orchestration (FR-PLAN, FR-SYN) wiring all four agents together. | A full end-to-end query returns a composite, explainable verdict using live data from all four agents. |
| Sprint 3 | Days 7–8 (4–5 Sept) | Bhashini integration (FR-LANG) starting with 2–3 languages; UI/map/trace visualisation (FR-UI). | A voice query in at least one non-English language returns a correct, spoken, mapped response end-to-end. |
| Sprint 4 | Day 9 (6–7 Sept) | Expand language coverage where time permits; integration testing, resilience checks against RISK-1, demo rehearsal. | Prototype is stable across a rehearsed demo script; known gaps are documented rather than hidden. |

## 7. Document Sign-off

This SRS forms the baseline for Sprint 0 of the Agile development plan. Subsequent design documents (High-Level Design and Low-Level Design) will be developed directly against the functional requirements (Section 3) and interfaces (Section 4) defined in this document.

| Role | Name | Signature / Date |
|---|---|---|
| Prepared by | Aditya Raj Arora |  |
| Reviewed by |  |  |
| Approved by (Faculty Mentor) |  |  |
