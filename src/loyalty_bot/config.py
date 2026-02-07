from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    payments_test_mode: bool = Field(default=False, alias="PAYMENTS_TEST_MODE")
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str
    payment_provider_token: str

    # Bot mode:
    #  - demo: seller demo funnel (landing payload, 7-day trial, no purchases)
    #  - brand: client branded bot (no demo/trial features)
    bot_mode: str = Field(default="demo", alias="BOT_MODE")

    # Admins can do anything (later: cancel campaigns, etc.)
    admin_tg_ids: str = ""

    # Seller allowlist (comma-separated TG user IDs). MVP: onboarding seller only if in this list.
    seller_tg_ids: str = ""

    database_dsn: str

    price_per_campaign_minor: int = 9900
    currency: str = "RUB"

    # Credits packs (campaign slots). Amounts are in minor units (kopeks).
    credits_pack_1_minor: int = 100_000
    credits_pack_3_minor: int = 289_000
    credits_pack_10_minor: int = 2_750_000

    send_batch_size: int = 50
    send_tick_seconds: int = 1
    tg_global_rate_per_sec: int = 25
    retry_base_seconds: int = 5
    retry_max_seconds: int = 3600

    log_level: str = "INFO"
    log_dir: str = "/app/logs"

    # --- SuperAdmin / Central metrics (optional) ---
    # If CENTRAL_DATABASE_DSN is not set, metrics push is disabled (no impact on bot/worker).
    central_database_dsn: str = Field(default="", alias="CENTRAL_DATABASE_DSN")
    # Unique id/name of this instance in SuperAdmin registry (recommended for brand bots).
    instance_id: str = Field(default="", alias="INSTANCE_ID")
    instance_name: str = Field(default="", alias="INSTANCE_NAME")
    metrics_push_interval_seconds: int = Field(default=60, alias="METRICS_PUSH_INTERVAL_SECONDS")

    @staticmethod
    def _parse_ids(value: str) -> set[int]:
        raw = (value or "").strip()
        if not raw:
            return set()
        return {int(x.strip()) for x in raw.split(",") if x.strip()}

    @property
    def admin_ids_set(self) -> set[int]:
        return self._parse_ids(self.admin_tg_ids)

    @property
    def seller_ids_set(self) -> set[int]:
        return self._parse_ids(self.seller_tg_ids)

    @property
    def is_demo_bot(self) -> bool:
        return (self.bot_mode or "").strip().lower() == "demo"


settings = Settings()
