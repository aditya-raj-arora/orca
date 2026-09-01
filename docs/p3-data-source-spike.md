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
| Active alerts: cyclone / severe weather (FR-WX-2) | **WeatherAPI.com** `forecast.json?...&alerts=yes` (lat/lon native) + **GDACS** GeoRSS for tropical cyclones | WeatherAPI: free key (email, no card); GDACS: none | high — both verified with a live key/feed 2026-09-01 |
| Data timestamp (FR-WX-3) | every Open-Meteo / WeatherAPI / GDACS payload carries an ISO time field | — | high |
| SST + chlorophyll (FR-OCEAN-2) | **INCOIS GeoServer WMS `GetFeatureInfo`** (JSON) | none | medium — SST verified, chl came back empty |
| Nearest PFZ + distance/bearing (FR-OCEAN-1, FR-OCEAN-3) | **unresolved** — no vector feed found yet | none | low — needs §4.2 |
| Staleness flag (FR-OCEAN-4) | derive from the layer/advisory publish date | — | medium |

Net: the **weather half is unblocked** — Open-Meteo (forecast + wave) and
GDACS are keyless; the WeatherAPI.com free key is obtained and its alert
samples captured. The app runs degraded (no alerts) if the alert sources are
unreachable, so nothing here blocks issue #10. The **Ocean Agent is partially
blocked** — SST is fine, but the PFZ geometry that `get_nearest_pfz()` needs
(LLD §4.3) has no confirmed source yet. The adapter contract (`AdapterResult`,
LLD §2.9) is unaffected either way, so schema-lock on Day 1 is not at risk.

---

## 2. Weather provider

HLD §6 / CREDENTIALS.md #3 offered **OpenWeather** or **IMD bulletins**.

- **OpenWeather** — dropped. One Call 3.0/4.0 both need the paid *"One Call by
  Call"* subscription (card on file even for the free 1k/day tier), the same
  constraint that pushed the LLM choice to Gemini (HLD v1.1 §6).
- **IMD API** (`api.imd.gov.in`) — evaluated in depth, then dropped. It *is*
  the authoritative Indian warnings source and its endpoints fit FR-WX-2 well
  (`/coastalbulletin`, `/districtwarning`, `/districtnowcast`,
  `/cyclone_track`, `/cyclone_wind`), but access requires: account →
  email verify → **"Complete Profile" vetting form** (ID proof upload +
  *permission letter from Head of Institute* + a 4-question declaration) →
  **manual review**. Too slow / too heavy for the sprint. Every endpoint 401s
  `{"error":"API key missing"}` until approved. Kept as a **later** option: if
  IMD's official wording is wanted, pull it from the **WMO Alert Hub**
  (`severeweather.wmo.int` / `alert-hub.org`), which re-publishes IMD CAP
  warnings with no IMD account.

Chosen stack — all card-free, mostly keyless:

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
- **WeatherAPI.com** — `https://api.weatherapi.com/v1/forecast.json`
  - **Free key, email signup, no card, no documents, instant.** Free tier
    ~1M calls/month (confirm on the dashboard). **Key transport: `?key=`
    query param** (no header variant) — confirmed against a live key.
  - `?q=<lat>,<lon>&days=3&alerts=yes&aqi=no` → `alerts.alert[]`
    (CAP-sourced government warnings) — FR-WX-2. Each alert object:
    `headline`, `msgtype`, `severity`, `urgency`, `areas`, `category`,
    `certainty`, `event`, `note`, `effective`, `expires`, `desc`,
    `instruction`. Adapter maps `event`/`headline` → `active_alerts[]`,
    keeps `severity` + `expires` for Risk/Safety + FR-WX-3.
  - **lat/lon native — no district-id mapping** (the main reason it beats IMD
    for this project). One call; response also carries `current` (wind, gust,
    vis, precip) as a cross-check on Open-Meteo.
  - **Verified 2026-09-01:** Kochi / Chennai / Kollam → HTTP 200,
    `alerts.alert: []` (no active alerts at capture time — the empty shape,
    committed under `docs/samples/weather/weatherapi_alerts_*.json`, trimmed
    to `location`+`current`+`alerts`).
  - India government-alert coverage is partial (depends what WeatherAPI
    ingests) — acceptable for a prototype; GDACS backstops cyclones.
- **GDACS GeoRSS** — `https://www.gdacs.org/xml/rss.xml`
  - **No key.** UN/EC-run global multi-hazard feed; includes **tropical
    cyclones** over the North Indian Ocean.
  - GeoRSS/XML: `<gdacs:eventtype>TC</gdacs:eventtype>`, `<geo:lat>`,
    `<geo:long>`, `<gdacs:alertlevel>` (Green/Orange/Red), affected-area
    polygon. Verified reachable 2026-09-01 (`rss.xml`, ~150 KB).
  - Adapter filters to `eventtype == TC` and distance from the query point.

### Decision

`WeatherDataAdapter.fetch()` (LLD §2.9) makes **up to four upstream calls**,
concurrently, and normalises them into one `dict`:

1. Open-Meteo Forecast → wind, precipitation, visibility
2. Open-Meteo Marine → wave height
3. WeatherAPI `alerts=yes` → `active_alerts` (`list[str]` in `WeatherResult`)
4. GDACS RSS → append any active TC near the point to `active_alerts`

Failure handling (FR-WX-4 / NFR-REL-2), to confirm with P1 since Risk/Safety
keys off `status` (LLD §4.2):

- **Calls 1 or 2 fail** → adapter returns `AdapterResult(status='unavailable')`.
  No partial/fabricated `WeatherResult`.
- **Calls 3 and 4 both fail / no WeatherAPI key** → return the forecast data
  with `active_alerts = []` **and a flag** (e.g. `alerts_source_unavailable=
  True` in the raw dict) so Synthesis can say "alert data unavailable" rather
  than imply "no alerts". Do **not** downgrade the whole result to
  `unavailable` just for missing alerts — but Risk/Safety must treat
  unknown-alerts as not-safe per NFR-REL-2. **P1 to rule on this exact policy
  at contract-lock.**
- If only one of 3/4 succeeds, use it and drop the flag.

`.env` keys: `WEATHER_FORECAST_BASE_URL`, `MARINE_API_BASE_URL`,
`WEATHERAPI_BASE_URL`, `WEATHERAPI_KEY`, `GDACS_BASE_URL` (see `.env.example`).

### Sample calls

```
GET https://api.open-meteo.com/v1/forecast
    ?latitude=9.93&longitude=76.26
    &current=wind_speed_10m,wind_gusts_10m,wind_direction_10m,precipitation,visibility
    &hourly=wind_speed_10m,precipitation,visibility&forecast_days=2&timezone=auto

GET https://marine-api.open-meteo.com/v1/marine
    ?latitude=9.93&longitude=76.26
    &current=wave_height,wave_direction,wave_period&hourly=wave_height&forecast_days=2

GET https://api.weatherapi.com/v1/forecast.json
    ?key=$WEATHERAPI_KEY&q=9.93,76.26&days=3&alerts=yes&aqi=no   # needs the free key

GET https://www.gdacs.org/xml/rss.xml                            # keyless
```

Open-Meteo + GDACS responses are committed under `docs/samples/weather/`. The
WeatherAPI sample needs the key — run
`WEATHERAPI_KEY=… scripts/p3_sample_calls.sh`.

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
1. **Open-Meteo (forecast + marine) + GDACS: done** — no key; samples committed
   under `docs/samples/weather/`.
2. **WeatherAPI.com: key obtained, samples captured** (Kochi/Chennai/Kollam,
   `alerts.alert: []` at capture time). Remaining:
   - Confirm the exact **free-tier rate limit** from the dashboard and note it
     in §2.
   - Capture one **populated-alert** example (any location with an active
     warning) so the adapter's `alerts.alert[]` mapping is tested against a
     non-empty payload — commit it alongside the others.
   - **Rotate the key** — it was shared in plaintext during setup. Regenerate
     on the WeatherAPI dashboard, put the new value only in local
     `src/backend/.env` (gitignored) and the Render secret.
3. **P1 to rule at contract-lock:** the "alerts unavailable / no key" policy in
   §2 — forecast still returned, `active_alerts=[]` + unavailable flag, and
   Risk/Safety treats unknown-alerts as not-safe (NFR-REL-2). Also: 4 upstream
   calls vs. NFR-PERF-1's 8 s single-agent budget — do them concurrently.
4. Retire `WEATHER_API_KEY` / `WEATHER_API_BASE_URL` from `render.yaml`
   (P1/P6) — no longer used. Add `WEATHERAPI_KEY` as a `sync: false` secret.
5. Later, optional: if IMD's official warning wording is wanted, add a WMO
   Alert Hub CAP reader (`severeweather.wmo.int`) — no IMD account needed.

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
Updated in this branch: weather block is `WEATHER_FORECAST_BASE_URL` /
`MARINE_API_BASE_URL` / `WEATHERAPI_BASE_URL` / `WEATHERAPI_KEY` /
`GDACS_BASE_URL`; `WEATHER_API_KEY` marked retired; IMD noted as evaluated and
dropped. INCOIS comment records "no key, WMS GetFeatureInfo + bulletin".
CREDENTIALS.md #3/#4 moved to "in progress". Flip #3 to ✓ once the WeatherAPI
key + sample land; #4 once §4.2 lands.

---

## 5. Impact on the LLD contracts (none breaking)

- `AdapterResult` / `WeatherResult` / `PFZResult` / `OceanParams` are all
  still adequate as written in LLD §2.3, §2.4, §2.9.
- One clarification to raise at contract-lock: `WeatherResult` has no
  `precipitation` / `visibility` field though FR-WX-1 lists them and
  Open-Meteo returns them. Decide add-now vs. defer (already flagged as a
  TODO in `app/schemas/weather.py`).
- `WeatherResult.active_alerts: list[str]` is adequate for WeatherAPI / GDACS
  alert strings, but consider whether a per-source "alert data unavailable"
  flag is needed so Synthesis can distinguish "no alerts" from "couldn't reach
  the alert sources" (NFR-REL-1).
- `OceanParams` handling `None` for chl is already in the contract — good,
  because chl will frequently be `None` in practice.
