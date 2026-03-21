from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    backend_url: str
    request_timeout: float

    @classmethod
    def from_env(cls) -> "Settings":
        bot_token = os.getenv("BOT_TOKEN", "").strip()
        if not bot_token:
            raise RuntimeError("BOT_TOKEN is not set.")

        return cls(
            bot_token=bot_token,
            backend_url=os.getenv("BACKEND_URL", "http://backend:8000").rstrip("/"),
            request_timeout=float(os.getenv("BOT_REQUEST_TIMEOUT", "10")),
        )


settings = Settings.from_env()
