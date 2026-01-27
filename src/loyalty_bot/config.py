from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    payments_test_mode: bool = Field(default=False, alias="PAYMENTS_TEST_MODE")
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str
    payment_provider_token: str

    # Admins can do anything (later: cancel campaigns, etc.)
    admin_tg_ids: str = ""

    # Seller allowlist (comma-separated TG user IDs). MVP: onboarding seller only if in this list.
    seller_tg_ids: str = ""

    database_dsn: str

    price_per_campaign_minor: int = 9900
    currency: str = "RUB"

    send_batch_size: int = 50
    send_tick_seconds: int = 1
    tg_global_rate_per_sec: int = 25
    retry_base_seconds: int = 5
    retry_max_seconds: int = 3600

    log_level: str = "INFO"

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


settings = Settings()
