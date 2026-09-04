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
| 3 | Weather: `WEATHER_FORECAST_BASE_URL` / `MARINE_API_BASE_URL` / `GDACS_BASE_URL` (no key) + `WEATHERAPI_KEY` (alerts) | P3 | ◑ forecast+wave+GDACS keyless & verified; only `WEATHERAPI_KEY` (free, email signup, no card — `weatherapi.com/signup.aspx`) still to get. See `docs/p3-data-source-spike.md` §2 |
| 4 | `INCOIS_BASE_URL` / `INCOIS_GEOSERVER_URL` / `INCOIS_PFZ_WFS_URL` (all keyless) | P3 | ✓ no API/key. SST+chl via GeoServer WMS `GetFeatureInfo`; PFZ advisory geometry via GeoServer WFS `PFZ_Automation:pfzlines` GeoJSON. Both sampled. See `docs/p3-data-source-spike.md` §3 |
| 5 | `GIS_BOUNDARY_DATA_PATH` (IMBL/MPA dataset) | P4 | ✓ no API/key. Marine Regions (VLIZ) India EEZ as IMBL proxy + WDPA/Protected Planet bulk country download (no token) for MPAs. See `docs/p4-data-source-spike.md` |
| 6 | `DATABASE_URL` | P1/P4 | auto locally (Docker); verified — `schema.sql` applies cleanly against local PostGIS (`docs/p4-data-source-spike.md` §5); ☐ for prod (Supabase, see below) |
| 7 | `RENDER_DEPLOY_HOOK_BACKEND` / `_FRONTEND` | P6 | ☐ (after Render setup) |

---

## 1. `LLM_API_KEY` — Planner entity extraction + Synthesis composition

Powers `PlannerAgent.extract_entities()` and `SynthesisAgent.compose()`
(LLD §2.2, §2.7). `LLM_PROVIDER` in `.env` picks which of these you're using.

**Default: Google Gemini** (via Google AI Studio) — chosen specifically because
**no team member has a paid LLM subscription**, and Gemini's free tier needs
no card on file (HLD v1.1 §6 records this as the reason for the deviation from
the original Claude/GPT assumption in HLD v1.0).

- Go to **aistudio.google.com/apikey** → sign in with a Google account →
  Create API key. That's it — no billing setup, no approval wait.
- Supports the function-calling/tool-use pattern the LLD assumes.
- Free-tier rate limits are generous enough for dev + a live demo at this
  project's scale (SRS §5.5); if they get tight during rehearsal, switch to
  the backup below rather than adding a paid plan under time pressure.

**Backup: Groq** — also free, no card, and notably fast (useful headroom
against NFR-PERF-1/2's 8s/15s budgets). Get a key at **console.groq.com** →
API Keys. Runs open models (Llama 3.x) with tool-calling support. Set
`LLM_PROVIDER=groq` to switch.

(Anthropic/OpenAI remain supported in `LLM_PROVIDER` for anyone who does have
a paid key later, but aren't the default for this team.)

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

Powers `WeatherDataAdapter` (LLD §2.9). HLD §6 named OpenWeather or IMD
bulletins; the spike settled it (see the blockquote below). The Data Access
Layer isolates the choice from the rest of the system (LLD §2.9 design note),
so this can still change without touching any agent.

Historical options considered:
- **OpenWeather** (`openweathermap.org/api`) — dropped: One Call 3.0/4.0
  require the paid *"One Call by Call"* plan (card on file).
- **IMD** (`api.imd.gov.in`) — evaluated, then dropped: access needs ID proof
  + an institute permission letter + manual review (spike §2).
- **Open-Meteo** (`open-meteo.com`) — forecast + wave, keyless.
- **WeatherAPI.com** + **GDACS** — alerts (FR-WX-2).

> **Spike outcome (2026-09-01, `docs/p3-data-source-spike.md` §2):** card-free,
> mostly keyless stack:
> - **Open-Meteo Forecast API** (`WEATHER_FORECAST_BASE_URL=https://api.open-meteo.com/v1`)
>   — wind / precipitation / visibility. **No key, no signup.** Verified.
> - **Open-Meteo Marine API** (`MARINE_API_BASE_URL=https://marine-api.open-meteo.com/v1`)
>   — wave height. **No key.** Verified.
> - **WeatherAPI.com** (`WEATHERAPI_BASE_URL=https://api.weatherapi.com/v1`) —
>   `forecast.json?...&alerts=yes` for government severe-weather / cyclone
>   alerts (FR-WX-2), lat/lon native. **Free key, email signup, no card, no
>   documents, instant** — `weatherapi.com/signup.aspx`. Only the alerts leg
>   depends on it; the app runs degraded without it.
> - **GDACS GeoRSS** (`GDACS_BASE_URL=https://www.gdacs.org/xml`) — tropical
>   cyclones over the North Indian Ocean. **No key.** Verified.
> - IMD stays available as a later add via the **WMO Alert Hub**
>   (`severeweather.wmo.int`), which re-publishes IMD CAP warnings with no IMD
>   account.

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

> **Spike outcome (2026-09-01, `docs/p3-data-source-spike.md` §3):**
> - **No REST API and no key.** Access is via OGC web services + bulletins.
> - **SST + chlorophyll (FR-OCEAN-2): confirmed & sampled** — GeoServer WMS
>   `GetFeatureInfo` with `INFO_FORMAT=application/json` against
>   `https://incois.gov.in/geoserver/PFZ-TUNA-SST-CHL/wms` (layers `sst`,
>   `chl`). Real SST values captured for Kochi/Kollam; watch the `GRAY_INDEX
>   = -1` / `null` no-data sentinels → must become `None`, never a guess.
>   Samples in `docs/samples/incois/`.
> - **PFZ advisory geometry (FR-OCEAN-1/3): confirmed & sampled** — GeoServer
>   **WFS → GeoJSON**, `PFZ_Automation:pfzlines` on
>   `https://incois.gov.in/geoserver/PFZ_Automation/ows`, keyless. 96
>   `MultiLineString` advisory lines, dated via `Year`+`Julian_day`. Found by
>   inspecting the PfzWebGis app's network calls. Adapter fetches once/day and
>   caches. Trimmed sample in `docs/samples/incois/`.
> - ERDDAP (`erddap.incois.gov.in`) is a clean JSON/CSV fallback for SST/chl;
>   some datasets are archival, verify recency.
> - Courtesy email to INCOIS confirming public-prototype use + attribution
>   ("PFZ advisories © INCOIS") is still worth sending, but no longer blocking.

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

> **Spike outcome (2026-09-02, `docs/p4-data-source-spike.md`):** resolved,
> both keyless:
> - **IMBL**: Marine Regions (VLIZ) Maritime Boundaries Geodatabase v12,
>   Indian EEZ (200 NM), MRGID 8480 — keyless WFS GeoJSON, used as the
>   open-data proxy for the maritime boundary line (the literal negotiated
>   IMBL isn't published as open geometry; see the spike doc §2 for the
>   distinction).
> - **MPA**: WDPA/Protected Planet — the REST API needs a free account/token,
>   but the monthly per-country bulk download
>   (`d1gam3xoknrgr2.cloudfront.net/current/WDPA_WDOECM_<Mon><Year>_Public_IND.zip`)
>   doesn't. Filtered to `REALM in {Marine, Coastal}`: 6 real MPAs (Sundarbans
>   NP, Chilika Lake, Sundarban Wetland, Gulf of Mannar Marine Biosphere
>   Reserve, Pichavaram Mangrove, Thane Creek).
> - Combined GeoJSON at `src/backend/data/gis/imbl_mpa_boundaries.geojson`
>   (7 features). `GISBoundaryAdapter.fetch()` loads/caches it.
>   `scripts/seed_geofence_boundaries.py` seeds `geofence_boundary` from it —
>   verified against a local `postgis/postgis:16-3.4` container: `schema.sql`
>   applies cleanly, seed produces 1 IMBL + 6 MPA rows, idempotent re-run.

## 6. `DATABASE_URL`

- **Local dev**: already set in `src/backend/.env.example` for
  `docker compose up` — points at the self-hosted `postgis/postgis` `db`
  service in `docker-compose.yml`. No signup needed.
- **Production (Supabase)**: Render's free Postgres doesn't support PostGIS,
  so production uses a free Supabase project instead. Create one at
  supabase.com, run `src/backend/app/db/schema.sql` in its SQL Editor, then
  copy the pooled connection string from Project Settings → Database →
  Connection string (swap the prefix to `postgresql+asyncpg://`). Full
  walkthrough: `docs/DEPLOYMENT.md` §A.

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
