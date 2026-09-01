# Deployment — Render

Owner: P6 (QA/Integration Lead). Reference: HLD v1.0 §7 (Deployment Architecture).

CI (lint/test) runs automatically on every PR — see `.github/workflows/backend-ci.yml`,
`frontend-ci.yml`. **Deploy is a separate, gated step**: Render never builds on
its own push trigger (`autoDeploy: false` in `render.yaml`); instead,
`deploy-backend.yml` / `deploy-frontend.yml` call a Render **deploy hook** only
after that service's CI workflow succeeds on `main`. This means broken code
can merge to a feature branch and fail CI without ever reaching Render, and
`main` itself can never deploy code that failed its own CI run.

## One-time setup (do this once, as the Render account owner)

1. **Create a Render account** (or sign in) at render.com — this has to be a
   human action, not something to script or hand credentials for.
2. **New > Blueprint** → connect the `aditya-raj-arora/orca` GitHub repo.
   Render reads `render.yaml` at the repo root and proposes 3 resources:
   `orca-db` (Postgres), `orca-backend` (Docker web service), `orca-frontend`
   (static site). Approve creation.
3. **Fill in secret env vars** flagged `sync: false` in `render.yaml`, in the
   Render dashboard for `orca-backend`: `LLM_API_KEY`, `BHASHINI_API_KEY`,
   `BHASHINI_USER_ID`, `WEATHER_API_KEY`, `WEATHER_API_BASE_URL`. See
   `src/backend/.env.example` for what each one is and who owns getting it
   (P2/P3 mostly, per SRS §6.4 dependencies).
4. **PostGIS check**: open `orca-db`'s Shell/psql in the Render dashboard and
   run `CREATE EXTENSION IF NOT EXISTS postgis;`. If this errors, the plan
   tier doesn't support it — see the TODO in `render.yaml`'s `databases:`
   block for the fallback (self-hosted PostGIS via `docker-compose.yml`).
5. **Get the frontend's URL** (Render assigns something like
   `orca-frontend.onrender.com` after first deploy) and set it as
   `CORS_ALLOWED_ORIGINS` on `orca-backend`, and as `VITE_API_BASE_URL` /
   `VITE_WS_BASE_URL` (pointing at `orca-backend`'s URL) on `orca-frontend`.
   This is a one-time chicken-and-egg step — trigger one manual deploy of
   each service first (Render dashboard > Manual Deploy) to get both URLs,
   then set these and redeploy.
6. **Copy each service's Deploy Hook URL** (Render dashboard > service >
   Settings > Deploy Hook) and add them as GitHub Actions secrets:
   - Repo → Settings → Secrets and variables → Actions → New repository secret
   - `RENDER_DEPLOY_HOOK_BACKEND` = orca-backend's hook URL
   - `RENDER_DEPLOY_HOOK_FRONTEND` = orca-frontend's hook URL

   (Alternatively, paste the two hook URLs to whoever's driving the repo
   setup and have them run `gh secret set RENDER_DEPLOY_HOOK_BACKEND` /
   `RENDER_DEPLOY_HOOK_FRONTEND` — these are one-off trigger URLs, not
   account credentials, but still treat them as secrets: anyone with the URL
   can trigger a deploy.)

After this, merging to `main` with green CI auto-deploys. No further manual
steps.

## Known risk — free-tier cold starts vs. NFR-PERF

Render's free plan spins services down after inactivity; a cold start can
take 30–60s, which blows past NFR-PERF-1/2 (8s/15s response budgets, SRS
§5.1). This is fine for async development but **not for a live demo**:
before demo day, either upgrade `orca-backend` to a paid always-on plan, or
plan to "wake" the service with a warm-up request a few minutes before
demoing. Track this as a Day 6 hardening item (see `docs/ORCA_SRS_v1.1.docx`
§6.5 Sprint 4 exit criteria).

## Local development

Local dev doesn't touch Render at all — use `docker compose up --build`
(root `docker-compose.yml`) or run backend/frontend separately per the root
`README.md` "Getting started" section.
