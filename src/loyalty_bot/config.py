from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str
    payment_provider_token: str
    admin_tg_ids: str = ""

    database_dsn: str

    price_per_campaign_minor: int = 9900
    currency: str = "RUB"

    send_batch_size: int = 50
    send_tick_seconds: int = 1
    tg_global_rate_per_sec: int = 25
    retry_base_seconds: int = 5
    retry_max_seconds: int = 3600

    log_level: str = "INFO"

    @property
    def admin_ids_set(self) -> set[int]:
        raw = (self.admin_tg_ids or "").strip()
        if not raw:
            return set()
        return {int(x.strip()) for x in raw.split(",") if x.strip()}


settings = Settings()
