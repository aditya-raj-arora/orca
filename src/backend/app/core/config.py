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

    weather_api_key: str = ""
    weather_api_base_url: str = ""

    incois_base_url: str = "https://incois.gov.in"

    gis_boundary_data_path: str = "./data/gis/imbl_mpa_boundaries.geojson"
    imbl_buffer_km: float = 5.0


@lru_cache
def get_settings() -> Settings:
    return Settings()


# TODO(P1): wire get_settings() into FastAPI via Depends() in main.py, and into
# each adapter's constructor rather than having adapters read os.environ directly
# — keeps config centralised and testable (mockable in unit tests).
