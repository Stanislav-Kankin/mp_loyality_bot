from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler


_DEFAULT_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


def setup_logging(*, level: str, service_name: str, log_dir: str = "/app/logs") -> None:
    """Configure logging to both stdout and a rotating file.

    Notes:
    - We keep stdout logs for `docker compose logs`.
    - We also write to /app/logs/<service>.log with rotation.
    - Designed to be lightweight and safe to call once at startup.
    """

    root = logging.getLogger()

    # Prevent duplicate handlers if setup_logging is called more than once.
    for h in list(root.handlers):
        root.removeHandler(h)

    log_level = getattr(logging, (level or "INFO").upper(), logging.INFO)
    root.setLevel(log_level)

    formatter = logging.Formatter(_DEFAULT_FORMAT)

    # Always log to stdout (docker).
    sh = logging.StreamHandler()
    sh.setLevel(log_level)
    sh.setFormatter(formatter)
    root.addHandler(sh)

    # Optional file logging.
    if log_dir:
        try:
            os.makedirs(log_dir, exist_ok=True)
            file_path = os.path.join(log_dir, f"{service_name}.log")
            fh = RotatingFileHandler(
                file_path,
                maxBytes=10 * 1024 * 1024,  # 10 MB
                backupCount=5,
                encoding="utf-8",
            )
            fh.setLevel(log_level)
            fh.setFormatter(formatter)
            root.addHandler(fh)
        except Exception:
            # If filesystem is read-only or volume is missing, don't crash the app.
            root.exception("Failed to set up file logging")

    # Reduce noise from some libraries unless user explicitly wants DEBUG.
    if log_level >= logging.INFO:
        logging.getLogger("aiogram.event").setLevel(logging.INFO)
        logging.getLogger("asyncpg").setLevel(logging.WARNING)
        logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
