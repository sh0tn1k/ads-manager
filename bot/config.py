from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BotConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str = Field(validation_alias="TELEGRAM_BOT_TOKEN")
    bot_api_base_url: str = Field(
        default="https://api.telegram.org",
        validation_alias="TELEGRAM_BOT_API_BASE_URL",
    )
    allowed_user_ids: str = Field(default="", validation_alias="BOT_ALLOWED_USER_IDS")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    fsm_path: str = Field(default="data/fsm.sqlite", validation_alias="BOT_FSM_PATH")

    @property
    def allowed_ids(self) -> set[int]:
        if not self.allowed_user_ids.strip():
            return set()
        result: set[int] = set()
        for part in self.allowed_user_ids.split(","):
            part = part.strip()
            if part:
                result.add(int(part))
        return result
