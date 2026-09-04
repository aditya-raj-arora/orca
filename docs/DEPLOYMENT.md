# Deployment — Render (app) + Supabase (database)

Owner: P6 (QA/Integration Lead). Reference: HLD v1.0 §7 (Deployment Architecture).

CI (lint/test) runs automatically on every PR — see `.github/workflows/backend-ci.yml`,
`frontend-ci.yml`. **Deploy is a separate, gated step**: Render never builds on
its own push trigger (`autoDeploy: false` in `render.yaml`); instead,
`deploy-backend.yml` / `deploy-frontend.yml` call a Render **deploy hook** only
after that service's CI workflow succeeds on `main`. This means broken code
can merge to a feature branch and fail CI without ever reaching Render, and
`main` itself can never deploy code that failed its own CI run.

The database is **Supabase, not Render's managed Postgres** — Render's free
Postgres plan doesn't include the PostGIS extension that `geofence_boundary`
(LLD §3) needs; Supabase's free tier does. `render.yaml` deliberately does not
provision a `databases:` resource — `DATABASE_URL` is set manually on
`orca-backend` instead.

## One-time setup

### A. Database (Supabase)

1. **Create a Supabase account** at supabase.com (human action — not
   something to script or hand credentials for) → **New project**. Pick a
   region close to your users/demo location, and set a strong database
   password (save it, you'll need it for the connection string).
2. Once the project is provisioned, open the **SQL Editor** (left sidebar) →
   New query → paste the full contents of `src/backend/app/db/schema.sql` →
   Run. It starts with `CREATE EXTENSION IF NOT EXISTS postgis;`, which will
   succeed on Supabase's free tier (unlike Render's).
3. Go to **Project Settings → Database → Connection string** → copy the
   **URI** under "Connection pooling" (Session mode — better suited to a
   single backend instance than Transaction mode). It looks like:
   ```
   postgresql://postgres.xxxxxxxx:[YOUR-PASSWORD]@aws-0-xx-xxxx-1.pooler.supabase.com:5432/postgres
   ```
   Swap in your actual DB password, and change the scheme prefix to
   `postgresql+asyncpg://` to match what `app/core/config.py` expects
   (SQLAlchemy's async driver) — i.e.:
   ```
   postgresql+asyncpg://postgres.xxxxxxxx:<password>@aws-0-xx-xxxx-1.pooler.supabase.com:5432/postgres
   ```
   This full string is your `DATABASE_URL`.

### B. App (Render)

4. **Create a Render account** (or sign in) at render.com.
5. **New > Blueprint** → connect the `aditya-raj-arora/orca` GitHub repo.
   Render reads `render.yaml` at the repo root and proposes 2 resources:
   `orca-backend` (Docker web service), `orca-frontend` (static site).
   Approve creation.
6. **Fill in secret env vars** flagged `sync: false` in `render.yaml`, in the
   Render dashboard for `orca-backend`:
   - `DATABASE_URL` — the Supabase connection string from step 3
   - `LLM_API_KEY`, `BHASHINI_API_KEY`, `BHASHINI_USER_ID` — see
     `docs/CREDENTIALS.md` for where to get each and who owns getting it
     (P2/P3 mostly, per SRS §6.4 dependencies)
   - `WEATHERAPI_KEY` — **optional, but set it.** It was optional when it only
     powered the severe-weather *alerts* leg of FR-WX-2. Since #116 the same
     response is also the standby for the wind/precipitation/visibility leg
     when Open-Meteo is rate-limited from Render's shared IP (see **Known
     risks**) — so on the deployed instance this key is often the difference
     between a weather answer and `INSUFFICIENT_DATA`. The app still runs
     without it, just with more ways to go unavailable (the
     `WeatherResult.alerts_source_available` flag reports which alert sources
     were actually reachable, so nothing silently reads as "no alerts").
     Free key, email signup, no card: `weatherapi.com/signup.aspx`.

   > **Watch the spelling: `WEATHERAPI_KEY`, not `WEATHER_API_KEY`.**
   > `WEATHER_API_KEY` / `WEATHER_API_BASE_URL` were the retired OpenWeather
   > plan, dropped from `render.yaml` at the 2026-09-01 contract-lock sync
   > (PR #28). They no longer exist in the blueprint and setting them does
   > nothing — this step used to name them, which is what #114 fixed.

   - `OPEN_METEO_API_KEY` — **optional, paid.** Leave unset unless someone has
     bought an Open-Meteo plan. It exists because Render's shared egress IP
     makes the keyless tier's per-IP quota unreliable — read "Open-Meteo 429s
     on Render" under **Known risks** before setting it, since the key does
     nothing unless the three Open-Meteo base URLs are moved to the
     `customer-` hosts at the same time.

   Everything else the backend needs is a non-secret `value:` in `render.yaml`
   (base URLs, `WEATHER_CACHE_TTL_SECONDS`, `GEOCODING_COUNTRY_CODE`, ...) and
   is created with the service automatically — nothing to type in by hand.
7. **Get the frontend's URL** (Render assigns something like
   `orca-frontend.onrender.com` after first deploy) and set it as
   `CORS_ALLOWED_ORIGINS` on `orca-backend`, and as `VITE_API_BASE_URL` /
   `VITE_WS_BASE_URL` (pointing at `orca-backend`'s URL) on `orca-frontend`.
   This is a one-time chicken-and-egg step — trigger one manual deploy of
   each service first (Render dashboard > Manual Deploy) to get both URLs,
   then set these and redeploy.
8. **Copy each service's Deploy Hook URL** (Render dashboard > service >
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

## Known risks

- **Free-tier cold starts vs. NFR-PERF**: Render's free plan spins services
  down after inactivity; a cold start can take 30–60s, which blows past
  NFR-PERF-1/2 (8s/15s response budgets, SRS §5.1). Supabase's free tier
  similarly pauses a project after a week of inactivity. Fine for async
  development but **not for a live demo**: before demo day, either upgrade
  `orca-backend` to a paid always-on plan, or "wake" both services (a
  warm-up request to the backend, and any query against Supabase) a few
  minutes before demoing. Track this as a Day 6 hardening item (see
  `docs/ORCA_SRS_v1.1.docx` §6.5 Sprint 4 exit criteria).
- **Split-provider footprint**: the app now depends on two free-tier vendors
  (Render + Supabase) instead of one — one more thing that can independently
  have an outage on demo day. Worth a quick connectivity check the morning of.
- **Open-Meteo 429s on Render are not our fault and not fully our fix**
  (#106, #116). Open-Meteo's keyless tier is metered **per client IP**, and it
  has no keys on that tier to meter instead. Render's free plan gives us no
  dedicated egress IP: we share one with every other free service on the node,
  so the quota is spent by traffic we neither generate nor can see, and once an
  hour/day bucket is empty it stays empty. The symptom is 429s that look
  constant and that no amount of local backoff clears — which is exactly the
  point, because backing off is not what refills someone else's bucket.

  What the code does about it (`data_access/http_client.py`, `weather_adapter.py`):
  falls back to the WeatherAPI `current` block — already fetched for alerts, so
  no extra request, and on a per-key quota our IP cannot exhaust — whenever
  Open-Meteo's forecast leg is refused, which is why `WEATHERAPI_KEY` matters
  more than its "optional" label suggests; caches results for `WEATHER_CACHE_TTL_SECONDS` on a ~5 km grid so repeat
  queries about one harbour cost one call; caches place-name lookups for the
  life of the process; sits out a 429 for `Retry-After` (or 30s) per source;
  and **never retries a 429** — an IP-level bucket cannot clear inside our
  sub-second budget, so a retry adds load without a chance of succeeding.
  Failures still degrade to `status='unavailable'` rather than to a made-up
  wind speed (FR-WX-4). None of this raises the quota.

  **The actual fix**, when a rate-limited demo is unacceptable, is to stop
  being metered by IP. In rough order of cost:

  1. **Buy an Open-Meteo plan** and set `OPEN_METEO_API_KEY` on `orca-backend`.
     The quota then follows the account. The key is only honoured on the
     `customer-` hosts, so set the base URLs **together with** it or the key is
     silently ignored (the adapter logs a warning if you do this):
     `WEATHER_FORECAST_BASE_URL=https://customer-api.open-meteo.com/v1`,
     `MARINE_API_BASE_URL=https://customer-marine-api.open-meteo.com/v1`,
     `GEOCODING_BASE_URL=https://customer-geocoding-api.open-meteo.com/v1`.
  2. **Route egress through a static IP** (Render paid plans, or a proxy add-on).
     Cheaper than a weather plan but buys a *private* free-tier quota, not a
     bigger one — fine for demo volume, still breakable by our own bursts.
  3. **Self-host Open-Meteo** — it is open source. No quota at all, but it wants
     a disk full of model data and is not a demo-week project.

  For demo day specifically, set `WEATHERAPI_KEY` first — it is free and it
  covers the observed failure. Option 2 plus the existing cache is usually
  enough after that; option 1 is for when weather has to be reliable.

  Wave height has no standby: WeatherAPI's marine data is a separate endpoint
  we do not call, so a rate-limited `marine-api.open-meteo.com` is still
  `unavailable` (FR-WX-4). It has not been the one failing — it is a different
  host with its own budget.

## Local development

Local dev doesn't touch Render or Supabase at all — use
`docker compose up --build` (root `docker-compose.yml`, which runs a
self-hosted `postgis/postgis` container) or run backend/frontend separately
per the root `README.md` "Getting started" section.
