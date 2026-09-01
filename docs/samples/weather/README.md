# Weather sample responses — captured 2026-09-01

Evidence for issue #3. Regenerate with `scripts/p3_sample_calls.sh`
(forecast + marine need no credentials; IMD needs a free `IMD_API_KEY`).

| File | Source | Key? |
|---|---|---|
| `openmeteo_forecast_<loc>.json` | Open-Meteo Forecast API — wind / gusts / direction / precipitation / visibility (FR-WX-1) | none |
| `openmeteo_marine_<loc>.json` | Open-Meteo Marine API — wave height / direction / period (FR-WX-1) | none |
| `imd_*.json` (after you add a key) | IMD API — cyclone track, subdivision/district warnings, nowcast, coastal bulletin (FR-WX-2) | `IMD_API_KEY` |

Locations: Kochi (9.93, 76.26), Chennai (13.08, 80.27), Kollam (8.88, 76.60).

## What the samples returned (2026-09-01, `current` block)

| Loc | wind km/h | gust km/h | precip mm | visibility m | wave m | wave period s |
|---|---|---|---|---|---|---|
| Kochi | 16.2 | 37.4 | 0.0 | 30960 | 1.26 | 10.15 |
| Chennai | 9.4 | 25.6 | 0.1 | 5760 | 0.70 | 8.90 |
| Kollam | 19.6 | 43.2 | 0.0 | 28180 | 1.30 | 10.85 |

Both Open-Meteo payloads carry an ISO `time` on every block → FR-WX-3.

## IMD (not captured yet — needs the key)

Every `api.imd.gov.in/api/v1/*` endpoint returns `{"error":"API key missing"}`
(HTTP 401) without a key. Register (free, no card) at
`https://api.imd.gov.in/register.php`, then:

```
IMD_API_KEY=your_key scripts/p3_sample_calls.sh
```

and commit the resulting `imd_*.json`. Note in `docs/p3-data-source-spike.md`
how the key is passed (request header vs `?api_key=` query param) — the script
currently assumes the query param and may need adjusting.
