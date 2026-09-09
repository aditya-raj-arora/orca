# P2 — Final prompt tuning + demo query rehearsal (#43)

Owner: P2. Requirements: FR-PLAN-1, FR-SYN-1, FR-SYN-2, FR-SYN-3, NFR-USE-2.

Rehearsed the 5-query demo script (see the runbook artifact) live against the
deployed backend (`orca-frontend-krth`/`orca-backend-krth`), 2026-09-09.

## Results

| Query | Result |
|---|---|
| "Is it safe to fish near Kochi tomorrow morning?" | SAFE, cited (weather + ocean + geofencing + risk_safety), forecast-window path exercised |
| "What's the sea like off Chennai right now?" | **Failed on first rehearsal** — see finding #1 below. Fixed. |
| "Which fishing zones near Mangalore should I avoid?" | SAFE, area-scoped nearby-list answer — but the phrasing conflated recommended fishing zones with "avoid". See finding #3. Fixed. |
| Known-unsafe point (9.19N 78.89E, inside Gulf of Mannar) | UNSAFE, stated plainly, not softened by calm-ish weather (FR-GEO-4 held) |
| "What's the safest route from Kochi to Colombo?" | SAFE + the #173 route caveat ("Note: only the location above was checked...") — confirmed working end to end live |

4 of 5 queries were correct and cited on the first pass. One failed outright;
investigating it surfaced two more real issues. All three are fixed in this
PR.

## Findings

### 1. Keyword-fallback vocabulary gap: "sea" — real failure, fixed

"What's the sea like off Chennai right now?" hit a **live Gemini 503
UNAVAILABLE ("high demand")** on both the first attempt and the #36 retry,
falling through to keyword matching (`_extract_via_keywords`). Its weather
keyword list — `("weather", "forecast", "wind", "wave", "temperature",
"rain", "conditions")` — doesn't include "sea", so the fallback matched
nothing, landed below `MIN_ENTITY_CONFIDENCE`, and the query got "I couldn't
quite understand that" for a plainly weather-shaped question.

**Fix:** added `"sea"` to the weather keyword tuple
(`app/orchestration/planner_agent.py`).

### 2. Gemini free-tier overload survives a single retry — real risk, hardened

The same rehearsal query hit a genuine, sustained Gemini 503 that outlasted
the single retry #36 added (`Planner.extract_entities: LLM extraction failed
..., retrying once` → `retry also failed ..., falling back to keyword
matching`, both in Render logs within ~2s of each other). A 504 timeout
clears on one retry; bursty "high demand" overload apparently doesn't always.

**Fix:** raised `extract_entities()`'s retry budget from 1 to 2 (three
attempts total), each later attempt bounded to the 10s API floor. Worst case
grows from ~35s to ~45s before falling back — long, but bounded, and only
reached on repeated genuine provider failure. Combined with finding #1, a
"sea" query surviving on keywords alone is also now a safety net if the LLM
is down for longer than 3 attempts can cover.

### 3. `ocean_nearby` / `geofencing_nearby` conflation — real answer quality issue, fixed

"Which fishing zones near Mangalore should I avoid?" correctly triggered both
lists (issue #174's area scope), and correctly said "No marine protected
areas were found within the 150.0 km search radius" — the honest answer to
"which zones to avoid". But it then also enumerated 8 `ocean_nearby` (PFZ)
zones by distance/bearing in the same breath, with nothing distinguishing
that these are *recommended fishing spots*, not restricted areas. Read
naively, a fisherman could come away thinking the listed PFZ zones are the
ones to avoid — the opposite of what they are.

**Fix:** added prompt rule 5a (`_SYNTHESIS_SYSTEM_PROMPT`,
`app/orchestration/synthesis_agent.py`) — `ocean_nearby` and
`geofencing_nearby` answer opposite questions (fishing opportunities vs.
restricted areas) and must never be blended into one undifferentiated list.

### 4. Raw bearing degrees — real phrasing issue, fixed

Both live answers with a bearing (`bearing_deg`) recited it as "at a bearing
of 232.2 degrees" / "bearing 210.8 degrees" — technically correct, not how a
fisherman talks or thinks. Added prompt rule 6: phrase `bearing_deg` as a
compass direction (e.g. "southwest") rather than raw degrees.

## Known gap, not fixed here (flagging, not scope for this issue)

`_deterministic_sentences()` (the no-LLM composition path, #139) has no
builder for `ocean_nearby` / `geofencing_nearby` at all — an area-scoped
query composed via the no-LLM path would silently drop the nearby-zone list
entirely rather than compose it deterministically. Prompt tuning can't fix
this (it's a code gap in the fallback, not the prompt); a proper fix is a new
`_ocean_nearby_sentences()` / `_geofencing_nearby_sentences()` pair mirroring
the existing builders. Worth its own follow-up if the team wants area-scoped
queries to survive a fully-dead LLM before or after the demo.

## Not covered by this rehearsal

Multilingual output (NFR-USE-2's "in the target language" AC) — all 5 demo
queries were run in English. `SARVAM_API_KEY` is missing from `render.yaml`
(flagged separately in #44's writeup), so a live non-English/voice rehearsal
isn't possible against the deployed instance right now.

## Testing

- 3 new tests (`test_planner_agent.py`): "sea" keyword-fallback recognition,
  two-failures-then-success retry survival.
- Full backend suite: `355 passed, 6 skipped`.
