from __future__ import annotations

import os
from dataclasses import dataclass


def _parse_int_set(value: str) -> set[int]:
    items: set[int] = set()
    for part in value.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            items.add(int(part))
        except ValueError:
            # ignore junk
            continue
    return items


@dataclass(frozen=True)
class Settings:
    bot_token: str
    superadmin_ids: set[int]
    central_database_dsn: str
    log_level: str


def load_settings() -> Settings:
    bot_token = os.getenv("SUPERADMIN_BOT_TOKEN", "").strip()
    if not bot_token:
        raise RuntimeError("SUPERADMIN_BOT_TOKEN is required for superadmin bot")

    superadmin_ids = _parse_int_set(os.getenv("SUPERADMIN_TG_IDS", ""))
    if not superadmin_ids:
        raise RuntimeError("SUPERADMIN_TG_IDS is required (comma-separated tg ids)")

    central_database_dsn = os.getenv("CENTRAL_DATABASE_DSN", "").strip()
    if not central_database_dsn:
        raise RuntimeError("CENTRAL_DATABASE_DSN is required")

    return Settings(
        bot_token=bot_token,
        superadmin_ids=superadmin_ids,
        central_database_dsn=central_database_dsn,
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )
