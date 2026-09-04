# Contributing to ORCA — Agile Workflow & Traceability Rules

This project runs a compressed Agile build (see `docs/ORCA_Chat_Summary.docx` for the
6-day schedule, `docs/ORCA_SRS_v1.1.docx` §6.5 for the original 9-day sprint plan).
Because the timeline is tight and six people are working in parallel on independently
owned modules, **the rules below exist to stop silent contract drift, not to slow you
down.** Follow them exactly; they are cheap when followed and expensive when skipped.

## 1. Golden rule: everything traces to a requirement

Every unit of work — issue, branch, commit, PR — must be traceable back to a
**Requirement ID** from the SRS (`FR-WX-1`, `NFR-SEC-2`, ...) or an explicit design
section (`LLD §2.6`, `HLD §3`). If you can't name the requirement your change
satisfies, stop and check `docs/ORCA_SRS_v1.1.docx` §3–5 before writing code.

For a genuinely non-functional change — team/ownership metadata, a typo fix,
CI config, anything the SRS/HLD/LLD simply don't govern — reference this
section (`CONTRIBUTING.md §1`) or the relevant process section instead
(e.g. `CONTRIBUTING.md §8` for an ownership-table update), or tag the PR
`[no-req]` explicitly. The CI traceability check (`.github/workflows/
traceability-check.yml`) accepts all of these; the point is to never merge a
PR with *no* traceability statement at all, not to force a fabricated
requirement ID onto something that isn't one.

This traceability is what lets us answer, at any point, "why does this code exist"
and "is requirement X actually implemented" — both of which matter for the SIH
evaluation and for catching scope drift before demo day.

## 2. Branching model

- `main` is always demo-able. It is protected — no direct pushes, no force-pushes,
  PRs only, at least one review required (see repo branch protection settings).
- Branch naming: `<owner>/<type>/<short-slug>-<req-id>`
  - `<owner>`: your role tag (`p1`, `p2`, `p3`, `p4`, `p5`, `p6`)
  - `<type>`: `feat`, `fix`, `chore`, `docs`, `test`
  - Example: `p3/feat/weather-agent-fr-wx-1`, `p4/fix/geofence-buffer-fr-geo-3`
- One branch = one module concern. Do not mix P1's orchestration changes with P4's
  DB schema changes in the same branch — it breaks the "vertical slice" ownership
  model from the Chat Summary (§2, §4) that lets everyone work without blocking
  each other.

## 3. Commit messages (Conventional Commits + requirement tag)

```
<type>(<scope>): <summary> [<REQ-ID>]

<optional body — why, not what>
```

Example:
```
feat(ocean-agent): compute nearest PFZ via haversine [FR-OCEAN-3]

Implements Section 4.3 of the LLD. Unit-tested against 3 fixture
locations near Kochi, Chennai, Kollam.
```

`<type>`: `feat`, `fix`, `docs`, `test`, `chore`, `refactor`.
`<scope>`: the module (`planner`, `weather-agent`, `geofence`, `frontend-chat`, …).

## 4. Pull requests

Use the PR template (auto-populated). Every PR must state:

1. **Requirement ID(s)** it implements or fixes.
2. **LLD section** it implements against (so a reviewer can diff behaviour vs. spec).
3. **Owner** (P1–P6) — for CODEOWNERS routing and so the integration lead (P1/P6,
   per Chat Summary §4) knows who to ask about contract changes.
4. Whether it changes a **shared data contract** (`WeatherResult`, `PFZResult`,
   `GeofenceResult`, `RiskVerdict`, any WebSocket message shape in LLD §5.2). If yes,
   tag `@aditya-raj-arora` (P1) — contract changes need a same-day broadcast to all
   owners per the Chat Summary's risk note: *"the main risk is silent contract drift."*

CI must be green before merge (see `.github/workflows/`). Squash-merge to keep `main`
history readable and traceable — the squash commit message must keep the `[REQ-ID]` tag.

## 5. Definition of Done (applies to every task)

A task is **not done** until:

- [ ] Code implements the exact method signature / schema from the LLD (or the LLD
      is updated in the same PR if you deviated — LLD §8 requires this).
- [ ] Unit tests exist for the module's core logic (especially Risk/Safety Agent and
      Planner routing — both are pure decision trees per LLD §4, so they must be
      independently unit-tested against fixtures, not just eyeballed).
- [ ] No fabricated data on failure — every adapter/agent returns an explicit
      `status='unavailable'`/`INSUFFICIENT_DATA` state rather than guessing
      (NFR-REL-1, NFR-REL-2, FR-WX-4, FR-OCEAN-4). This is a safety-critical rule,
      not a style preference — do not relax it under time pressure.
  - [ ] Secrets are read from environment variables only, never hardcoded (NFR-SEC-2).
- [ ] PR is linked to its requirement ID(s) and merged into `main` via a reviewed PR.

## 6. Daily contract-lock discipline

Per the Chat Summary (§4), Day 1 is a **contract-lock sync**: the shared dataclasses
in `src/backend/app/schemas/` and the WebSocket message shapes (LLD §5.2) must not
change casually after Day 1. If you need to change a shared schema:

1. Open an issue tagged `contract-change` describing the change and why.
2. Get P1 (integration lead) to confirm no other owner is blocked by it.
3. Update the schema, the LLD reference comment above it, and notify all owners
   whose modules consume it, same day.

## 7. Issue tracking

Use the **User Story** issue template for anything mapping to an FR/NFR, and the
**Bug Report** template for defects found during integration (Day 5 in the Chat
Summary schedule) or testing. Every issue must carry:

- A `req:FR-XXX` or `req:NFR-XXX` label (add the label if it doesn't exist yet).
- An `owner:P1`…`owner:P6` label.
- A `sprint:N` label matching SRS §6.5 / the 6-day schedule in the Chat Summary.

This is what makes the GitHub Project board a live traceability matrix, not just a
task list.

## 8. Onboarding a new teammate

1. Add them as a collaborator on the repo.
2. Replace the matching `*unassigned — fill in*` row in `README.md`'s ownership table
   with their name.
3. Replace the matching placeholder GitHub handle in `CODEOWNERS`.
4. Point them at: `docs/ORCA_SRS_v1.1.md` → `docs/ORCA_HLD_v1.1.md` →
   `docs/ORCA_LLD_v1.0.md` (in that order — the `.md` files are readability
   mirrors of the `.docx` originals, easiest to skim on GitHub) → their
   module's `TODO` comments in `src/`.

## 9. Style / tooling (enforced by CI)

- Backend: Python 3.11+, `ruff` for lint, `pytest` for tests, type hints required on
  all public functions (Pydantic models for all data contracts, per LLD §2).
- Frontend: TypeScript, `eslint` + `prettier`, component tests with `vitest`/
  `@testing-library/react`.
- Both CI pipelines run on every PR against `main` (`.github/workflows/`).
