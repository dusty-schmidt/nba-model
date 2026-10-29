"""Settings, read from environment variables (or a .env file); .env.example lists them."""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "nba-model"
    log_level: str = "INFO"
    # The SQLite store, the archetype meta, the raw pull cache and the browser profile (never in git).
    data_dir: Path = ROOT / "data"
    # The Chromium the NBA.com pulls drive. Only some builds pass Akamai (see app/ingest/nba_official.py);
    # empty uses Playwright's own (`uv run --group ingest playwright install chromium`).
    chrome_path: str = ""


settings = Settings()
