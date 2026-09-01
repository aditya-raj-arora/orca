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
| Wind, precipitation, visibility, alerts (FR-WX-1, FR-WX-2) | **OpenWeather One Call API 3.0** | yes (free) | high |
| Wave height (FR-WX-1) | **Open-Meteo Marine API** (OpenWeather has no wave height on the free tier) | no | high |
| Data timestamp (FR-WX-3) | both payloads carry epoch/ISO timestamps | — | high |
| SST + chlorophyll (FR-OCEAN-2) | **INCOIS GeoServer WMS `GetFeatureInfo`** (JSON) | no | medium — SST verified, chl came back empty |
| Nearest PFZ + distance/bearing (FR-OCEAN-1, FR-OCEAN-3) | **unresolved** — no vector feed found yet | no | low — needs §4.2 |
| Staleness flag (FR-OCEAN-4) | derive from the layer/advisory publish date | — | medium |

Net: the **Weather Agent is unblocked now** (pending a free key P3 must
create). The **Ocean Agent is partially blocked** — SST is fine, but the PFZ
geometry that `get_nearest_pfz()` needs (LLD §4.3) has no confirmed source
yet. The adapter contract (`AdapterResult`, LLD §2.9) is unaffected either
way, so schema-lock on Day 1 is not at risk.

---

## 2. Weather provider

HLD §6 / CREDENTIALS.md #3 offered **OpenWeather** or **IMD bulletins**.

### Findings

- **OpenWeather One Call API 3.0** (`https://api.openweathermap.org/data/3.0/onecall`)
  - Free tier: key issued immediately, no card, 1000 calls/day (enough for
    dev + demo at SRS §5.5 scale).
  - `current` + `hourly` + `daily` give **wind speed/gust/deg, rain, snow,
    visibility, UVI**. `alerts[]` gives **government weather alerts**
    (cyclone / high-wave / lightning warnings) with `sender_name`, `event`,
    `start`, `end`, `description` — this is FR-WX-2.
  - Every block has a `dt` epoch timestamp → FR-WX-3.
  - **No wave height / sea state** on this API. That is the one gap.
- **IMD** (`mausam.imd.gov.in`): bulletins are HTML/PDF, no clean REST. Higher
  implementation cost and more fragile (scraping). Kept only as a
  cross-check source for cyclone bulletins, not the primary feed.
- **Open-Meteo Marine API** (`https://marine-api.open-meteo.com/v1/marine`):
  free, **no key**, returns `wave_height`, `wave_direction`, `wave_period`
  (+ wind-wave / swell split) as `current` and `hourly`. Verified call:
  Kochi → `wave_height: 1.26 m` at `2026-09-01T08:45`. Fills the OpenWeather
  gap cleanly.

### Decision

Use **OpenWeather One Call 3.0 as the primary weather feed** and **Open-Meteo
Marine for wave height**. `WeatherDataAdapter.fetch()` (LLD §2.9) does both
calls and normalises them into one `dict`; `WeatherAgent.get_conditions()`
maps that to `WeatherResult`. Two upstreams = two failure modes: if either
call fails, per FR-WX-4 the adapter returns
`AdapterResult(status='unavailable')` rather than a partial/fabricated
`WeatherResult` (confirm the exact partial-data policy with P1, since
Risk/Safety keys off `status` — LLD §4.2).

`.env` for this:

```
WEATHER_API_KEY=<openweather key>
WEATHER_API_BASE_URL=https://api.openweathermap.org/data/3.0
# wave height: Open-Meteo Marine, no key, base https://marine-api.open-meteo.com/v1
```

### Sample call

```
GET https://api.openweathermap.org/data/3.0/onecall
    ?lat=9.93&lon=76.26&units=metric&exclude=minutely&appid=$WEATHER_API_KEY
```

Not committed yet — needs P3's key. Run `scripts/p3_sample_calls.sh` with
`WEATHER_API_KEY` exported; it writes `docs/samples/weather/openweather_onecall_kochi.json`.

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
| `https://las.incois.gov.in/` | Live Access Server | ASCAT winds etc. — not needed, OpenWeather covers wind |
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

### 4.1 Weather (P3, self-serve — no blocker)
1. Create a free OpenWeather account, subscribe to **One Call API 3.0**, copy
   the key into local `src/backend/.env`.
2. Run `WEATHER_API_KEY=… scripts/p3_sample_calls.sh`, commit the captured
   `docs/samples/weather/*.json`.
3. Confirm with **P2/P1**: two weather upstreams (OpenWeather + Open-Meteo) is
   acceptable vs. NFR-PERF-1 (8 s single-agent budget) — two sequential HTTPS
   calls, should be fine, but note it.

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
Updated in this branch: `WEATHER_API_BASE_URL` default filled in, INCOIS
comment now records "no key, WMS GetFeatureInfo + bulletin". CREDENTIALS.md
#3/#4 moved to "in progress" with notes. Flip to ✓ once 4.1 and 4.2 land.

---

## 5. Impact on the LLD contracts (none breaking)

- `AdapterResult` / `WeatherResult` / `PFZResult` / `OceanParams` are all
  still adequate as written in LLD §2.3, §2.4, §2.9.
- One clarification to raise at contract-lock: `WeatherResult` has no
  `precipitation` / `visibility` field though FR-WX-1 lists them and
  OpenWeather returns them. Decide add-now vs. defer (already flagged as a
  TODO in `app/schemas/weather.py`).
- `OceanParams` handling `None` for chl is already in the contract — good,
  because chl will frequently be `None` in practice.
