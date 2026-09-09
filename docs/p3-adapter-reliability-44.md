# P3 — Final adapter reliability check (#44)

Owner: P3. Requirements: FR-WX-1..4, FR-OCEAN-1..4, NFR-REL-1/2. LLD §2.9.

The repeatable check is `scripts/adapter_reliability_check.py` (no LLM key, no
DB — data adapters only). Run it locally *and* from the deployed backend's
shell; the deployed run is the one that matters, because Open-Meteo
rate-limits per egress IP and Render's free plan shares that IP
(`docs/DEMO_FALLBACK.md` Scenario A).

```
cd src/backend && python ../../scripts/adapter_reliability_check.py
```

## Acceptance criteria

### 1. All external endpoints reachable; keys present as Render secrets

| Source | Endpoint | Local run (2026-09-09) |
|---|---|---|
| Open-Meteo forecast | `api.open-meteo.com/v1/forecast` | ok, `forecast_source=open-meteo` (no fallback needed) |
| Open-Meteo marine | `marine-api.open-meteo.com/v1/marine` | ok |
| WeatherAPI alerts | `api.weatherapi.com/v1/forecast.json` | reachable (needs `WEATHERAPI_KEY`) |
| GDACS | `gdacs.org/xml/rss.xml` | ok |
| INCOIS PFZ (WFS) | `incois.gov.in/geoserver/PFZ_Automation/ows` | ok — 47 advisory lines |
| INCOIS SST/chl (WMS) | `incois.gov.in/geoserver/PFZ-TUNA-SST-CHL/wms` | ok — SST published for Kochi/Kollam, chlorophyll not published for any demo pixel today (`None`, legitimate per FR-OCEAN-2) |
| GIS boundaries | bundled `data/gis/imbl_mpa_boundaries.geojson` | ok — 6 MPA + 24 IMBL features |

**Render secrets** — declared in `render.yaml` as `sync: false` (Render prompts
on first deploy; `sync: false` does NOT mean a value was entered — verify in
the Render dashboard): `DATABASE_URL`, `LLM_API_KEY`, `WEATHERAPI_KEY`,
`BHASHINI_API_KEY`, `BHASHINI_USER_ID`. Optional / leave unset unless needed:
`OPEN_METEO_API_KEY` (paid; only if the shared-IP quota bites),
`OUTBOUND_PROXY_URL`.

- **Finding (not P3 / not blocking this issue):** `SARVAM_API_KEY` is read by
  `app/language/bhashini_client.py` (Sarvam AI is the ASR/TTS provider) but is
  **not** in `render.yaml`. If the demo uses voice input, P1/P2 need to add it
  as a Render secret. Retired keys `WEATHER_API_KEY` / `WEATHER_API_BASE_URL`
  are correctly absent.

### 2. Fresh cached PFZ snapshot committed as the demo fallback

Done — `scripts/refresh_pfz_snapshot.sh` re-run 2026-09-09:
`src/backend/data/snapshots/pfz_latest.json.gz`, 47 features, advisory
Year=2026 Julian_day=252 (i.e. today). Verified it loads and parses via
`INCOISAdapter._load_pfz_snapshot`. Re-run this daily-ish and re-commit until
demo day. There is no separate "weather snapshot" — the weather adapter has no
snapshot fallback (its resilience is the WeatherAPI leg + graceful
`unavailable`); nothing to prime there.

### 3. One live run per demo location returns `status='ok'` with sane values

Done for the data adapters — `adapter_reliability_check.py` passes for Kochi,
Chennai, Kollam (wind 6–13 km/h, wave 0.8–1.0 m, SST 30–32 °C where
published). The **full pipeline** run (adds Planner + Synthesis, needs
`LLM_API_KEY`) is `scripts/e2e_live_check.py` — run that once on the deployed
box too:

```
cd src/backend && LLM_API_KEY=... python ../../scripts/e2e_live_check.py
```

## Still requires a human (deploy access / keys)

- [ ] Run both scripts **from the Render backend shell** — local passes don't
      prove Render's shared egress IP isn't rate-limited by Open-Meteo.
- [ ] Confirm the `sync: false` secrets above actually have values in the
      Render dashboard.
- [ ] `LLM_API_KEY`-gated `e2e_live_check.py` pass (2 of the 3 scenarios need
      Gemini).
