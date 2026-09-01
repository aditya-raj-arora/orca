## Requirement ID(s)

<!-- Required (enforced by the traceability-check CI job). e.g. FR-OCEAN-3 -->

## Design reference

<!-- e.g. LLD §2.4, §4.3 -->

## Owner

<!-- P1 / P2 / P3 / P4 / P5 / P6 -->

## Summary

<!-- What changed and why. -->

## Shared contract changes?

- [ ] This PR changes a shared data contract (schemas in `src/backend/app/schemas/`,
      the WebSocket message shapes in LLD §5.2, or `schema.sql`). If checked,
      @aditya-raj-arora (P1) has been notified per CONTRIBUTING.md §6.

## Definition of Done (CONTRIBUTING.md §5)

- [ ] Matches the LLD method signature/schema, or the LLD was updated in this PR
- [ ] Unit tests added/updated
- [ ] No fabricated data on failure — explicit `unavailable`/`INSUFFICIENT_DATA` states used
- [ ] No secrets committed; new env vars added to `.env.example`
- [ ] CI is green
