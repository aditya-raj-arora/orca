# P3 Data-Source Access Spike — Weather + INCOIS

Closes the investigation half of **issue #3** ("[P3] Confirm INCOIS + weather
API access; sample calls").

- **Requirement(s):** FR-WX-1..4, FR-OCEAN-1..4
- **Design ref:** LLD §2.9 (Data Access Layer), HLD v1.1 §6, SRS §6.4
- **Owner:** P3 (Swaraj Rane)
- **Date:** 2026-09-01
- **Status:** access methods characterised + samples captured. Two decisions
  still need the team / an external email — see §4.

Raw captured responses live in [`docs/samples/`](samples/). Reproduce with
[`scripts/p3_sample_calls.sh`](../scripts/p3_sample_calls.sh).

---

## 1. Summary / recommendation

| Need | Source decided | Key? | Confidence |
|---|---|---|---|
| Wind, precipitation, visibility (FR-WX-1) | **Open-Meteo Forecast API** | **none** | high — verified |
| Wave height (FR-WX-1) | **Open-Meteo Marine API** | **none** | high — verified |
| Active alerts: cyclone / lightning / high-wave (FR-WX-2) | **IMD API** (`api.imd.gov.in`) — cyclone track + district/subdivision warnings + nowcast + coastal/sea bulletins | free key (registration) | medium — endpoints documented, key not yet obtained |
| Data timestamp (FR-WX-3) | every Open-Meteo + IMD payload carries an ISO/`time` field | — | high |
| SST + chlorophyll (FR-OCEAN-2) | **INCOIS GeoServer WMS `GetFeatureInfo`** (JSON) | none | medium — SST verified, chl came back empty |
| Nearest PFZ + distance/bearing (FR-OCEAN-1, FR-OCEAN-3) | **unresolved** — no vector feed found yet | none | low — needs §4.2 |
| Staleness flag (FR-OCEAN-4) | derive from the layer/advisory publish date | — | medium |

Net: the **weather forecast half is fully unblocked, no account needed**
(Open-Meteo). **FR-WX-2 alerts need a free IMD API key** — the app can run
degraded (no alerts) until it lands, so it doesn't block issue #10. The
**Ocean Agent is partially blocked** — SST is fine, but the PFZ geometry that
`get_nearest_pfz()` needs (LLD §4.3) has no confirmed source yet. The adapter
contract (`AdapterResult`, LLD §2.9) is unaffected either way, so schema-lock
on Day 1 is not at risk.

---

## 2. Weather provider

HLD §6 / CREDENTIALS.md #3 offered **OpenWeather** or **IMD bulletins**. We
started on OpenWeather One Call, then dropped it: One Call 3.0/4.0 both need a
paid *"One Call by Call"* subscription (card on file even for the free 1k/day
tier), which is exactly the constraint that pushed the LLM choice to Gemini
(HLD v1.1 §6). Chosen instead: **Open-Meteo + IMD**, both card-free.

### Findings

- **Open-Meteo Forecast API** — `https://api.open-meteo.com/v1/forecast`
  - **No key, no signup, no card.** 10 000 calls/day, non-commercial.
  - `current` + `hourly` give **wind speed / gusts / direction,
    precipitation, visibility** (+ temp, cloud, pressure) — FR-WX-1.
  - ISO `time` on every block → FR-WX-3.
  - Verified 2026-09-01, Kochi: `wind_speed_10m: 16.2 km/h`,
    `wind_gusts_10m: 37.4`, `precipitation: 0.0 mm`, `visibility: 30960 m`.
- **Open-Meteo Marine API** — `https://marine-api.open-meteo.com/v1/marine`
  - Same terms (no key/signup/card).
  - `wave_height`, `wave_direction`, `wave_period` (+ wind-wave / swell
    split), `current` and `hourly` — FR-WX-1.
  - Verified: Kochi → `wave_height: 1.26 m`, `wave_period: 10.15 s`.
- **IMD API** — `https://api.imd.gov.in/api/v1/...`
  - India's official met agency; the *authoritative* source for cyclone /
    high-wave / thunderstorm warnings in Indian waters — the right feed for
    FR-WX-2, and on-theme for an ISRO disaster-management brief.
  - **Free, no card, but needs a free account + API key** from
    `api.imd.gov.in/register.php`. Every endpoint returns
    `{"error":"API key missing"}` (HTTP 401) without it — verified. Key
    transport (header vs query param) and any approval delay are only
    visible after registering.
  - Relevant endpoints (from `api.imd.gov.in/public/api_reference.html`):
    | Endpoint | Gives |
    |---|---|
    | `/cyclone_track`, `/cyclone_wind` (GeoJSON), `/cyclone_cou` | active cyclone position / wind-threshold polygons / forecast cone |
    | `/districtwarning`, `/subdivisionwarning` | 5-day colour-coded warnings (thunderstorm/lightning, heavy rain, high wave) |
    | `/districtnowcast`, `/stationnowcast` | real-time nowcast warning categories |
    | `/coastalbulletin`, `/seabulletin`, `/portwarning` | fishermen / coastal wind + sea-state + visibility warnings |
  - Warnings are keyed by **district / subdivision / station / sea-area id**,
    not lat/lon → the adapter needs a lat/lon → id lookup (see §2 decision).

### Decision

`WeatherDataAdapter.fetch()` (LLD §2.9) makes **three upstream calls** and
normalises them into one `dict`:

1. Open-Meteo Forecast → wind, precipitation, visibility
2. Open-Meteo Marine → wave height
3. IMD → active alerts (`active_alerts: list[str]` in `WeatherResult`)

Failure handling (FR-WX-4 / NFR-REL-2), to confirm with P1 since Risk/Safety
keys off `status` (LLD §4.2):

- **Calls 1 or 2 fail** → adapter returns `AdapterResult(status='unavailable')`.
  No partial/fabricated `WeatherResult`.
- **Call 3 (IMD) fails or no key configured** → return the forecast data with
  `active_alerts = []` **and a flag** (e.g. `alerts_source_unavailable=True`
  in the raw dict) so Synthesis can say "alert data unavailable" rather than
  imply "no alerts". Do **not** downgrade the whole result to `unavailable`
  just because alerts are missing — but Risk/Safety must treat unknown-alerts
  as not-safe per NFR-REL-2. **P1 to rule on this exact policy at contract-lock.**

lat/lon → IMD id: ship a small static lookup of coastal districts /
sea-area ids (issue #10 scope), start with the ~8 demo locations, widen later.

`.env` keys: `WEATHER_FORECAST_BASE_URL`, `MARINE_API_BASE_URL`,
`IMD_API_BASE_URL`, `IMD_API_KEY` (see `.env.example`).

### Sample calls

```
GET https://api.open-meteo.com/v1/forecast
    ?latitude=9.93&longitude=76.26
    &current=wind_speed_10m,wind_gusts_10m,wind_direction_10m,precipitation,visibility
    &hourly=wind_speed_10m,precipitation,visibility&forecast_days=2&timezone=auto

GET https://marine-api.open-meteo.com/v1/marine
    ?latitude=9.93&longitude=76.26
    &current=wave_height,wave_direction,wave_period&hourly=wave_height&forecast_days=2

GET https://api.imd.gov.in/api/v1/subdivisionwarning        # 401 until IMD_API_KEY is set
```

Open-Meteo responses are committed under `docs/samples/weather/`. The IMD
sample needs the key — run `IMD_API_KEY=… scripts/p3_sample_calls.sh`.

---

## 3. INCOIS — PFZ / oceanographic data

CREDENTIALS.md #4 / SRS §6.4: *"confirm API vs. scraping"*. Answer: **no REST
API, no key; a mix of OGC web services (WMS) and published bulletins.**

### 3.1 What exists

| Endpoint | Type | Use for us |
|---|---|---|
| `https://incois.gov.in/geoserver/PFZ-TUNA-SST-CHL/wms` | GeoServer **WMS** (raster) — layers `sst`, `chl` | **SST/chl values** via `GetFeatureInfo` JSON |
| `https://incois.gov.in/geoserver/PFZ-TUNA-SST-CHL/wfs` | GeoServer WFS | nothing — **0 vector feature types** in this workspace |
| `https://incois.gov.in/geoserver/ows` (global) | GeoServer | **403** — only per-workspace virtual services are open |
| `https://erddap.incois.gov.in/erddap/` | **ERDDAP** griddap (JSON/CSV/netCDF) | fallback for SST/chl; 17 datasets, several archival |
| `https://las.incois.gov.in/` | Live Access Server | ASCAT winds etc. — not needed, Open-Meteo covers wind |
| `https://incois.gov.in/MarineFisheries/…` | HTML app | daily PFZ advisory — **not a plain GET** (see 3.3) |

### 3.2 SST + chlorophyll (FR-OCEAN-2) — SOLVED, key-free

```
GET https://incois.gov.in/geoserver/PFZ-TUNA-SST-CHL/wms
    ?service=WMS&version=1.1.1&request=GetFeatureInfo
    &layers=sst&query_layers=sst
    &info_format=application/json
    &srs=EPSG:4326&bbox=<minx,miny,maxx,maxy>&width=256&height=256&x=128&y=128
```

Response: `FeatureCollection` with one feature, `properties.GRAY_INDEX` = the
value at that pixel. Verified 2026-09-01:

| Point | SST | CHL |
|---|---|---|
| Kochi | `32.45` °C | `null` |
| Kollam | `30.17` °C | `null` |
| Chennai | `-1` (no data) | `null` |

**Adapter rules this forces (FR-OCEAN-4 / NFR-REL-1):**
- `GRAY_INDEX == -1` or `<= -900` → treat as missing → `None`.
- `GRAY_INDEX == null` → missing → `None`.
- Never interpolate or carry forward a stale value silently; if the whole
  call fails, return `AdapterResult(status='unavailable')`.
- Chlorophyll via WMS is **not yet reliable** (empty at all 3 points).
  Before Sprint 1: try a `TIME=` param for the latest mosaic date, else fall
  back to ERDDAP `IRS_chlorophyll_datasets`, else treat chl as best-effort /
  often-`None` (allowed — `OceanParams.chlorophyll_mg_m3` is `float | None`).

### 3.3 PFZ centroids / advisory geometry (FR-OCEAN-1, FR-OCEAN-3) — NOT SOLVED

`OceanAgent.get_nearest_pfz()` (LLD §2.4, §4.3) needs a set of **currently
published PFZ centroids** to run haversine against. Not found yet:

- No WFS vector layer for PFZ lines/points in the open workspace.
- `pfz_tuna_chl_sld` is a *styled raster*, not vector geometry.
- `MarineFisheries/TextData?secid=SEC001..SEC014` → HTTP 302 to a content-less
  landing page. The advisory needs the right params (date + sub-sector) or a
  form POST, or is published only as dated PDF bulletins.

This is the one true open item from SRS §6.4 — see §4.2.

### 3.4 Staleness (FR-OCEAN-4)

Whatever the PFZ source, it carries an advisory/issue date. Compare it to
`now()`; flag `is_stale` when older than a configurable threshold. Add a
setting (e.g. `ocean_pfz_staleness_hours`, default 48) to `app/core/config.py`
— that file is P1-owned, so raise it at the Day-1 contract sync rather than
editing it in a P3 branch.

---

## 4. Open items for the team

### 4.1 Weather (P3)
1. **Open-Meteo (forecast + marine): nothing to do** — no key, samples already
   committed under `docs/samples/weather/`.
2. **IMD API key** (needed for FR-WX-2 alerts, not for FR-WX-1):
   - Register at `https://api.imd.gov.in/register.php` (free, no card). Note
     whether it's instant or needs approval.
   - Put the key in local `src/backend/.env` as `IMD_API_KEY`, run
     `IMD_API_KEY=… scripts/p3_sample_calls.sh`, commit the IMD samples.
   - From the samples, confirm how the key is passed (header vs `?api_key=`)
     and note it in this doc for the adapter.
3. **P1 to rule at contract-lock:** the "IMD unavailable / no key" policy in
   §2 — forecast still returned, `active_alerts=[]` + unavailable flag, and
   Risk/Safety treats unknown-alerts as not-safe (NFR-REL-2). Also: 3
   sequential upstream calls vs. NFR-PERF-1's 8 s single-agent budget (fine,
   but do them concurrently in the adapter).
4. Retire `WEATHER_API_KEY` / `WEATHER_API_BASE_URL` from `render.yaml`
   (P1/P6) — no longer used.

### 4.2 INCOIS PFZ geometry — needs a decision (blocks part of issue #15)
Pick one, ideally at the Day-1 sync with P1:
- **(a)** Inspect `incois.gov.in/MarineFisheries/MarineFisheryAdvisory` in
  browser devtools → find the real XHR the "PFZ advisory" form fires, and
  whether it can return coordinates. ~1–2 h. Best outcome if it works.
- **(b)** Email INCOIS user services (`incois.gov.in` → Contact / User
  Services) requesting the PFZ advisory feed or daily shapefile/GeoJSON.
  Do this **today** regardless of (a) — reply latency is the risk (SRS §6.4).
- **(c)** Fallback for the prototype: parse the daily PFZ **PDF/text bulletin**
  per coastal sector into `(lat, lon, issued_at)` centroids. Ugly but
  self-contained; keeps `INCOISAdapter` the only place that knows the format
  (LLD §2.9), so swapping in (a)/(b) later doesn't touch `OceanAgent`.
- **(d)** Last resort for demo only: a checked-in snapshot of one day's PFZ
  centroids as GeoJSON, loaded by the adapter when live fetch fails —
  **must** be surfaced as `status='stale'`, never presented as live
  (NFR-REL-1). Flag explicitly to P1/P6, don't let it become silent scope.

### 4.3 `.env.example` / `CREDENTIALS.md`
Updated in this branch: weather block switched to
`WEATHER_FORECAST_BASE_URL` / `MARINE_API_BASE_URL` / `IMD_API_BASE_URL` /
`IMD_API_KEY`; `WEATHER_API_KEY` marked retired. INCOIS comment records "no
key, WMS GetFeatureInfo + bulletin". CREDENTIALS.md #3/#4 moved to
"in progress". Flip #3 to ✓ once the IMD key + sample land; #4 once §4.2 lands.

---

## 5. Impact on the LLD contracts (none breaking)

- `AdapterResult` / `WeatherResult` / `PFZResult` / `OceanParams` are all
  still adequate as written in LLD §2.3, §2.4, §2.9.
- One clarification to raise at contract-lock: `WeatherResult` has no
  `precipitation` / `visibility` field though FR-WX-1 lists them and
  Open-Meteo returns them. Decide add-now vs. defer (already flagged as a
  TODO in `app/schemas/weather.py`).
- `WeatherResult.active_alerts: list[str]` is adequate for IMD alert strings,
  but consider whether a per-source "alert data unavailable" flag is needed so
  Synthesis can distinguish "no alerts" from "couldn't reach IMD" (NFR-REL-1).
- `OceanParams` handling `None` for chl is already in the contract — good,
  because chl will frequently be `None` in practice.
