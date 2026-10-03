from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_ADS_API_BASE_URL = "https://promoteapi.telegram.org"


class AdsApiConfig(BaseSettings):
    """Credentials and endpoints for the Telegram Ads API (not the Bot API)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    access_token: str = Field(validation_alias="TELEGRAM_ADS_ACCESS_TOKEN")
    account_id: str | None = Field(default=None, validation_alias="TELEGRAM_ADS_ACCOUNT_ID")
    base_url: str = Field(
        default=DEFAULT_ADS_API_BASE_URL,
        validation_alias="TELEGRAM_ADS_API_BASE_URL",
    )
    timeout_seconds: float = 60.0
    upload_timeout_seconds: float = 120.0
    max_retries: int = 3

    @field_validator("account_id", mode="before")
    @classmethod
    def _empty_account_id(cls, value: str | None) -> str | None:
        if value is None or str(value).strip() == "":
            return None
        return str(value)
