from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- APIs externas
    odds_api_key: str
    api_football_key: str

    # --- Telegram
    telegram_bot_token: str
    telegram_chat_id: int

    # --- Google Sheets
    google_service_account_json_path: Path = Path("./credentials/sheets-sa.json")
    google_sheet_id: str

    # --- Base de datos
    database_url: str = "sqlite:///data/betting_bot.db"

    # --- Dead-man's switch
    healthchecks_url: str

    # --- Operación
    log_level: str = "INFO"
    timezone: str = "America/Bogota"

    # --- Moneda del sistema
    currency: str = "COP"

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"log_level debe ser uno de {allowed}")
        return upper

    @field_validator("currency")
    @classmethod
    def _validate_currency(cls, v: str) -> str:
        allowed = {"COP", "USD"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"currency debe ser uno de {allowed}")
        return upper

    @model_validator(mode="after")
    def _validate_google_sa_path(self) -> Settings:
        p = self.google_service_account_json_path
        if not p.is_absolute():
            self.google_service_account_json_path = _PROJECT_ROOT / p
        return self

    @property
    def config_dir(self) -> Path:
        return _PROJECT_ROOT / "config"

    @property
    def data_dir(self) -> Path:
        return _PROJECT_ROOT / "data"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
