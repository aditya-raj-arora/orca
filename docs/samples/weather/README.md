# Weather sample responses — captured 2026-09-01

Evidence for issue #3. Regenerate with `scripts/p3_sample_calls.sh`
(forecast + marine + GDACS need no credentials; alerts need a free
`WEATHERAPI_KEY`).

| File | Source | Key? |
|---|---|---|
| `openmeteo_forecast_<loc>.json` | Open-Meteo Forecast API — wind / gusts / direction / precipitation / visibility (FR-WX-1) | none |
| `openmeteo_marine_<loc>.json` | Open-Meteo Marine API — wave height / direction / period (FR-WX-1) | none |
| `gdacs_rss.xml` | GDACS GeoRSS — tropical-cyclone events, North Indian Ocean (FR-WX-2) | none |
| `weatherapi_alerts_<loc>.json` (after you add a key) | WeatherAPI.com `forecast.json?...&alerts=yes` — govt severe-weather / cyclone alerts (FR-WX-2) | `WEATHERAPI_KEY` |

Locations: Kochi (9.93, 76.26), Chennai (13.08, 80.27), Kollam (8.88, 76.60).

## What the samples returned (2026-09-01, `current` block)

| Loc | wind km/h | gust km/h | precip mm | visibility m | wave m | wave period s |
|---|---|---|---|---|---|---|
| Kochi | 16.2 | 37.4 | 0.0 | 30960 | 1.26 | 10.15 |
| Chennai | 9.4 | 25.6 | 0.1 | 5760 | 0.70 | 8.90 |
| Kollam | 19.6 | 43.2 | 0.0 | 28180 | 1.30 | 10.85 |

Both Open-Meteo payloads carry an ISO `time` on every block → FR-WX-3.

## GDACS

`gdacs_rss.xml` is the raw GeoRSS. The adapter filters
`<gdacs:eventtype>TC</gdacs:eventtype>` and distance from the query point;
`<gdacs:alertlevel>` is Green / Orange / Red. Often empty of TC events when no
system is active over the North Indian Ocean — that is the expected "no
cyclone" state, not a failure.

## WeatherAPI alerts

`weatherapi_alerts_<loc>.json` — captured 2026-09-01 with a live key, then
**trimmed to `location` + `current` + `alerts`** (the raw `forecast.json`
3-day payload is ~105 KB; we don't need it here). Key goes in `?key=` (query
param, no header variant). All three locations returned `alerts.alert: []` —
no active warnings at capture time; that's the empty shape.

Still to capture: one **populated-alert** example (a location with an active
warning) so the adapter mapping is tested against a non-empty `alerts.alert[]`.
Regenerate with `WEATHERAPI_KEY=your_key scripts/p3_sample_calls.sh`.
Documented `alerts.alert[]` fields: see `docs/p3-data-source-spike.md` §2.
