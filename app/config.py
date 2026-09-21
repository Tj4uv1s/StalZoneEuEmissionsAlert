"""Читання параметрів із змінних оточення (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _split(value: str) -> list[str]:
    """Розділити строку з комами на список."""
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Config:
    """Конфігурація застосунку з параметрів оточення."""

    api_base: str = os.getenv("STALCRAFT_API_BASE", "https://dapi.stalcraft.net")
    api_client_id: str = os.getenv("STALCRAFT_CLIENT_ID", "1")
    api_client_secret: str = os.getenv(
        "STALCRAFT_CLIENT_SECRET", "E98cm6J9NNjTQopph0c2eIXNKafg4R1Cjz0TZh2D"
    )
    regions: list[str] = field(default_factory=lambda: _split(os.getenv("REGIONS", "EU")))
    min_poll_interval: float = float(os.getenv("MIN_POLL_INTERVAL", "2"))

    tg_token: str = os.getenv("TG_TOKEN", "")
    tg_chat_ids: list[str] = field(default_factory=lambda: _split(os.getenv("TG_CHAT_IDS", "")))

    state_path: str = os.getenv("STATE_PATH", "data/state.json")

    def require(self) -> None:
        """Перевірити, що критичні параметри задані."""
        missing = [
            name
            for name, value in {
                "STALCRAFT_CLIENT_ID": self.api_client_id,
                "STALCRAFT_CLIENT_SECRET": self.api_client_secret,
            }.items()
            if not value
        ]
        if missing:
            raise RuntimeError(f"Не задані змінні оточення: {', '.join(missing)}")


config = Config()
