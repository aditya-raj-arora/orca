# Credentials & Data Sources — Where to Get Each One

This turns SRS v1.1 §6.4 ("Dependencies Requiring Early Confirmation") into an
actionable checklist. Nothing here is a secret itself — this just says where
to go and who owns getting it. Real values go in `src/backend/.env` (never
committed) locally, and in the Render dashboard for deployment (see
`docs/DEPLOYMENT.md`).

| # | Env var | Owner | Status |
|---|---|---|---|
| 1 | `LLM_API_KEY` | P2 | ☐ |
| 2 | `BHASHINI_API_KEY` / `BHASHINI_USER_ID` | P2 | ☐ |
| 3 | `WEATHER_API_KEY` / `WEATHER_API_BASE_URL` | P3 | ☐ |
| 4 | `INCOIS_BASE_URL` (access method) | P3 | ☐ |
| 5 | `GIS_BOUNDARY_DATA_PATH` (IMBL/MPA dataset) | P4 | ☐ |
| 6 | `DATABASE_URL` | P1/P4 | auto (local Docker or Render) |
| 7 | `RENDER_DEPLOY_HOOK_BACKEND` / `_FRONTEND` | P6 | ☐ (after Render setup) |

---

## 1. `LLM_API_KEY` — Planner entity extraction + Synthesis composition

Powers `PlannerAgent.extract_entities()` and `SynthesisAgent.compose()`
(LLD §2.2, §2.7). `LLM_PROVIDER` in `.env` picks which of these you're using.

- **Anthropic (Claude)**: console.anthropic.com → Settings → API Keys →
  Create Key. Needs a billing method on file (pay-as-you-go); check current
  rate limits under Settings → Limits before demo day.
- **OpenAI (GPT)**: platform.openai.com → API keys → Create new secret key.
  Same billing caveat.

Either works with the function-calling/tool-use pattern the LLD assumes — pick
one and confirm it against a real call (a plain "hello" completion is enough
for Day 1) before building the extraction prompt on top of it.

## 2. Bhashini — ASR / TTS / Language ID

Powers `BhashiniClient` (LLD §2.1). This is a **named enabling technology in
the problem statement**, so worth getting sorted early (SRS RISK-2).

- Go to **bhashini.gov.in** → look for "Get API Access" / the developer
  portal (Bhashini's public API access is via **ULCA** — the Universal
  Language Contribution API platform, model.ulca.gov.in / bhashini API hub).
- Register as a developer; you'll typically get a `userID` and an
  `ulcaApiKey` (maps to `BHASHINI_USER_ID` / `BHASHINI_API_KEY` here) after
  requesting inference-endpoint access for ASR, TTS, and translation
  pipelines.
- This can involve an approval step (not instant) — this is the dependency
  most likely to take longer than a day, so it should be the very first
  thing P2 requests, even before writing any Bhashini integration code.
- If sandbox/dev access is slow to arrive, unblock the rest of the team by
  hardcoding 1–2 sample transcripts in `BhashiniClient` behind a feature flag
  so Planner/Synthesis work isn't blocked — but flag this explicitly as a
  known gap for the demo, don't let it become silent scope creep.

## 3. Weather / marine data provider

Powers `WeatherDataAdapter` (LLD §2.9). HLD §6 names two options — pick one:

- **OpenWeather Marine/Weather API**: openweathermap.org/api → Sign Up → free
  tier gives an API key immediately (`WEATHER_API_KEY`); check which specific
  endpoint gives wind/wave/precipitation/visibility (One Call API 3.0 is the
  usual pick) and set `WEATHER_API_BASE_URL` to its base (e.g.
  `https://api.openweathermap.org/data/3.0`).
- **IMD public bulletins**: mausam.imd.gov.in — these are typically public
  HTML/structured bulletins rather than a clean REST API; if you go this
  route, confirm the actual data shape first (may need light scraping,
  which changes the adapter's implementation but not its contract).

Either is fine per HLD — the Data Access Layer isolates the choice from the
rest of the system (LLD §2.9 design note). No signup approval delay expected
with OpenWeather; IMD may need more digging into exact bulletin URLs.

## 4. INCOIS — Potential Fishing Zone / oceanographic data

Powers `INCOISAdapter` (LLD §2.9). **This is the SRS §6.4 item explicitly
flagged as "confirm API vs. scraping"** — do this before writing
`ocean_agent.py` logic.

- Start at **incois.gov.in** → look for the PFZ advisory service page and any
  published API/data-download links (INCOIS publishes PFZ advisories,
  sometimes as downloadable bulletins/shapefiles rather than a REST API).
- If no public API exists, the adapter will need to parse published bulletins
  — confirm the format (PDF? HTML table? GIS layer?) and get one successful
  sample retrieval before Sprint 1 starts (SRS §6.5 Sprint 0 exit criteria).
- No API key needed if it's public bulletin data — `INCOIS_BASE_URL` is just
  the base URL you're fetching from.

## 5. GIS boundary data — IMBL and MPA

Powers `GISBoundaryAdapter` / `geofence_boundary` table (LLD §2.9, §3). This
is **SRS RISK-4** — blocks the Geofencing Agent entirely until resolved, so
it's a Day 1 priority for P4.

- **IMBL (India's maritime boundary line)**: search for a public GeoJSON/
  shapefile — sources to check: the Ministry of External Affairs, National
  Hydrographic Office, or aggregator sites like Marine Regions
  (marineregions.org, has EEZ/boundary layers you can filter to India).
- **MPAs (Marine Protected Areas)**: the Ministry of Environment, Forest and
  Climate Change (MoEFCC) / Wildlife Institute of India publish protected
  area boundaries; Protected Planet (protectedplanet.net) is a good
  aggregator with a downloadable API/shapefile filtered by country.
- Whatever you find, convert/save it as GeoJSON at the path in
  `GIS_BOUNDARY_DATA_PATH` (`src/backend/.env.example`), WGS84 (EPSG:4326) to
  match `geofence_boundary.geometry`'s `GEOMETRY(Geometry, 4326)` column
  (LLD §3).

## 6. `DATABASE_URL`

No signup needed:

- **Local dev**: already set in `src/backend/.env.example` for
  `docker compose up` — points at the `db` service in `docker-compose.yml`.
- **Render**: auto-populated by `render.yaml`'s `fromDatabase` reference once
  `orca-db` exists — nothing to copy manually.

## 7. Render deploy hooks

Not needed until you're ready to deploy — see `docs/DEPLOYMENT.md` steps 1–6
for the full one-time Render setup, ending in the two hook URLs that become
`RENDER_DEPLOY_HOOK_BACKEND` / `RENDER_DEPLOY_HOOK_FRONTEND` GitHub secrets.

---

## Once you have a key

1. Put it in your **local** `src/backend/.env` (copied from `.env.example`,
   already gitignored — never commit the real file).
2. For deployment, add it in the Render dashboard for `orca-backend` (the
   `sync: false` vars in `render.yaml`).
3. Update the status column above (☐ → ✓) in a PR so the team can see at a
   glance what's still blocking — this table doubles as the SRS §6.4
   tracking checklist.
