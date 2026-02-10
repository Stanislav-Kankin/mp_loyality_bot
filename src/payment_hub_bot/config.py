from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class HubSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env.hub", env_file_encoding="utf-8", extra="ignore")

    bot_token: str = Field(alias="BOT_TOKEN")
    payment_provider_token: str = Field(alias="PAYMENT_PROVIDER_TOKEN")
    central_database_dsn: str = Field(alias="CENTRAL_DATABASE_DSN")

    hub_bot_username: str = Field(default="", alias="HUB_BOT_USERNAME")
    currency: str = Field(default="RUB", alias="CURRENCY")

    hub_pack_1_minor: int = Field(default=100_000, alias="HUB_PACK_1_MINOR")
    hub_pack_3_minor: int = Field(default=289_000, alias="HUB_PACK_3_MINOR")
    hub_pack_10_minor: int = Field(default=2_750_000, alias="HUB_PACK_10_MINOR")
    # optional for future pricing
    hub_pack_30_minor: int = Field(default=7_500_000, alias="HUB_PACK_30_MINOR")

    order_ttl_seconds: int = Field(default=86_400, alias="ORDER_TTL_SECONDS")

    payments_test_mode: bool = Field(default=False, alias="PAYMENTS_TEST_MODE")

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_dir: str = Field(default="/app/logs", alias="LOG_DIR")

    def pack_amount_minor(self, pack_code: str) -> int | None:
        mapping = {
            "pack_1": self.hub_pack_1_minor,
            "pack_3": self.hub_pack_3_minor,
            "pack_10": self.hub_pack_10_minor,
            "pack_30": self.hub_pack_30_minor,
        }
        return mapping.get((pack_code or "").strip())


hub_settings = HubSettings()
