"""Чтение настроек из переменных окружения (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _split(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Config:
    api_base: str = os.getenv("STALCRAFT_API_BASE", "https://dapi.stalcraft.net")
    api_token: str = os.getenv("STALCRAFT_TOKEN", "")
    regions: list[str] = field(default_factory=lambda: _split(os.getenv("REGIONS", "RU")))
    poll_interval: int = int(os.getenv("POLL_INTERVAL", "45"))

    tg_token: str = os.getenv("TG_TOKEN", "")
    tg_chat_ids: list[str] = field(default_factory=lambda: _split(os.getenv("TG_CHAT_IDS", "")))

    state_path: str = os.getenv("STATE_PATH", "data/state.json")

    def require(self) -> None:
        missing = [
            name
            for name, value in {
                "STALCRAFT_TOKEN": self.api_token,
                "TG_TOKEN": self.tg_token,
                "TG_CHAT_IDS": self.tg_chat_ids,
            }.items()
            if not value
        ]
        if missing:
            raise RuntimeError(f"Не заданы переменные окружения: {', '.join(missing)}")


config = Config()
