# Security Policy

ORCA is a Smart India Hackathon 2026 prototype (SIH26176, ISRO — Disaster
Management theme). It's early-stage, but it does handle user-submitted
location data and third-party API credentials, so please report security
issues responsibly rather than opening a public issue.

## Reporting a vulnerability

**Do not open a public GitHub issue for a security vulnerability.**

Instead, report it privately to the team lead:

- **Mukta Motwani** ([@m123ukta](https://github.com/m123ukta)) — Team Lead
- Or the Backend/Orchestration Lead, **Aditya Raj Arora** ([@aditya-raj-arora](https://github.com/aditya-raj-arora)), for anything touching the Gateway, session handling, or credential flow

You can also use GitHub's private vulnerability reporting for this repo
(**Security** tab → **Report a vulnerability**), which notifies maintainers
without making the report public.

Please include:

- A description of the issue and its potential impact.
- Steps to reproduce, or a proof of concept if you have one.
- The affected component (e.g. `FastAPI Gateway`, `BhashiniClient`, a
  specific agent/adapter — see [`docs/TRACEABILITY.md`](docs/TRACEABILITY.md)
  for the requirement → module → owner mapping).

We'll acknowledge reports as quickly as the team's availability allows given
the compressed build timeline (SRS §6.5) — this is a small student team, not
a company with a dedicated security response SLA, so please be patient.

## Scope notes

- This is a **prototype**, not a production system with paying users or
  long-term data retention guarantees. See [`docs/ORCA_SRS_v1.1.md`](docs/ORCA_SRS_v1.1.md)
  §5.4 for the security/privacy requirements it's actually held to
  (NFR-SEC-1..3): no persisted voice recordings beyond ASR, secrets only via
  environment variables, and location data shared only with the specific
  data source it's needed for.
- Known, tracked gaps are visible in [`docs/CREDENTIALS.md`](docs/CREDENTIALS.md)
  and the repo's [Dependabot alerts](https://github.com/aditya-raj-arora/orca/security/dependabot) —
  no need to re-report an issue already tracked there; a PR against the
  relevant open issue is more useful than a fresh report.
- Secret scanning and code scanning (CodeQL) require GitHub Advanced
  Security, which isn't enabled on this private repo — so please don't
  assume either is running automatically. Manual review is the current line
  of defense; report anything you spot.
