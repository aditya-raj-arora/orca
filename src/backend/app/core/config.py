"""
App configuration, loaded from environment variables (NFR-SEC-2 — never hardcode
secrets or commit them). See src/backend/.env.example for the full variable list
and which module/owner each one belongs to.

Owner: P1 (Backend/Orchestration Lead).
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"
    cors_allowed_origins: str = "http://localhost:5173"

    database_url: str = "postgresql+asyncpg://orca:orca@localhost:5432/orca"

    # google | groq | anthropic | openai — google is default: no paid
    # subscription needed. See docs/CREDENTIALS.md #1.
    llm_provider: str = "google"
    llm_api_key: str = ""

    bhashini_api_key: str = ""
    bhashini_user_id: str = ""
    bhashini_base_url: str = "https://bhashini.gov.in/api"

    # Weather / marine (owner P3, docs/p3-data-source-spike.md §2). All card-free;
    # only weatherapi_key needs a (free, no-card) key. weather_api_key /
    # weather_api_base_url are retained but unused — the OpenWeather plan was
    # dropped.
    # Blessed as the canonical set at the P1 contract-lock sync (2026-09-01):
    # weather_forecast_base_url, marine_api_base_url, weatherapi_base_url,
    # weatherapi_key, gdacs_base_url, incois_geoserver_url, incois_pfz_wfs_url,
    # ocean_pfz_staleness_hours (see PR #28). render.yaml updated to match —
    # drops weather_api_key/weather_api_base_url, adds weatherapi_key.
    weather_api_key: str = ""
    weather_api_base_url: str = ""
    weather_forecast_base_url: str = "https://api.open-meteo.com/v1"
    marine_api_base_url: str = "https://marine-api.open-meteo.com/v1"
    weatherapi_base_url: str = "https://api.weatherapi.com/v1"
    weatherapi_key: str = ""
    gdacs_base_url: str = "https://www.gdacs.org/xml"
    # Place name -> coordinates for the Planner (#110). Keyless, same provider
    # as forecast/marine. geocoding_country_code constrains the search: without
    # it "Kochi" resolves to Kochi, JAPAN rather than Kochi, India — see
    # data_access/geocoding_adapter.py's module docstring before changing it.
    # "" searches globally.
    geocoding_base_url: str = "https://geocoding-api.open-meteo.com/v1"
    geocoding_country_code: str = "IN"
    # Rate-limit protection for the keyless Open-Meteo tier (#106). Repeat
    # queries about the same ~5 km cell inside this window are served from
    # WeatherDataAdapter's in-process cache instead of spending quota. 10 min
    # sits under Open-Meteo's ~15 min model refresh, so a cache hit is never
    # older than the numbers a live call would have returned. Set to 0 to
    # disable the cache entirely (every fetch goes upstream).
    weather_cache_ttl_seconds: float = 600.0
    # Open-Meteo commercial key (#116). Empty = the keyless tier, which is
    # metered per CLIENT IP — and on Render's free plan that IP is shared with
    # every other service on the node, so the quota can be exhausted by traffic
    # that isn't ours and 429s look permanent. A key moves forecast / marine /
    # geocoding onto the account's own quota, but ONLY on the `customer-`
    # hosts: setting this WITHOUT also pointing the three base URLs below at
    # customer-api.open-meteo.com / customer-marine-api.open-meteo.com /
    # customer-geocoding-api.open-meteo.com leaves the key ignored (the adapter
    # logs a warning if you do). Costs money — see docs/DEPLOYMENT.md before
    # setting it.
    open_meteo_api_key: str = ""

    # The other way out of IP metering (#151): route the metered calls through
    # a static egress IP (e.g. Fixie) so the quota is measured against an
    # address only we use, instead of Render's shared node IP. Unset, nothing
    # changes and every call goes out directly.
    #
    # CONTAINS CREDENTIALS (http://user:pass@host:port) — env only, never
    # committed, and never logged. data_access/http_client.py is careful not
    # to put it in a message; keep it that way.
    #
    # Deliberately scoped to a source prefix rather than applied to all
    # outbound traffic: only Open-Meteo is metered per client IP. GDACS is a
    # 1.5 MB feed and WeatherAPI is metered per key, so putting either on a
    # bandwidth-metered proxy spends the plan for no benefit.
    outbound_proxy_url: str = ""
    outbound_proxy_sources: str = "open-meteo/"

    incois_base_url: str = "https://incois.gov.in"
    incois_geoserver_url: str = "https://incois.gov.in/geoserver/PFZ-TUNA-SST-CHL"
    incois_pfz_wfs_url: str = "https://incois.gov.in/geoserver/PFZ_Automation/ows"
    ocean_pfz_staleness_hours: float = 48.0
    # Demo-resilience (#38 / HLD §9 RISK-1): if the live PFZ WFS is unreachable
    # and nothing is in the per-day cache, INCOISAdapter serves this bundled
    # snapshot as status='stale'. Refresh with scripts/refresh_pfz_snapshot.sh.
    # TODO(P1): confirm at the next contract-lock touch (additive).
    incois_pfz_snapshot_path: str = "./data/snapshots/pfz_latest.json.gz"

    gis_boundary_data_path: str = "./data/gis/imbl_mpa_boundaries.geojson"
    imbl_buffer_km: float = 5.0

    # Risk/Safety Agent marginal-conditions thresholds (owner P4, LLD §4.2 /
    # Figure 2 "Weather conditions marginal ... no active alert?" branch).
    # At or above either value, with no active alert and no geofence
    # violation, the verdict is CAUTION rather than SAFE. Configurable, not
    # hardcoded in risk_safety_agent.py — the LLD gives the branch but no
    # numbers, so these are the team's defaults and are expected to be tuned.
    risk_marginal_wind_kmh: float = 25.0
    risk_marginal_wave_m: float = 2.0


@lru_cache
def get_settings() -> Settings:
    return Settings()


# TODO(P1): wire get_settings() into FastAPI via Depends() in main.py, and into
# each adapter's constructor rather than having adapters read os.environ directly
# — keeps config centralised and testable (mockable in unit tests).
