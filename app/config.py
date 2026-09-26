"""Читання параметрів із змінних оточення (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from app.constants import DEFAULT_ANNOUNCE_MAX_AGE, DEFAULT_TOP_SALES_DELAY

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
    announce_max_age: float = float(
        os.getenv("ANNOUNCE_MAX_AGE", str(DEFAULT_ANNOUNCE_MAX_AGE))
    )
    """Секунди: подію старіше цього не анонсуємо, тільки записуємо в стан.
    Дає тихий старт і не дає перезапуску анонсувати активний викид вдруге."""

    top_sales_delay: float = float(
        os.getenv("TOP_SALES_DELAY", str(DEFAULT_TOP_SALES_DELAY))
    )
    """Секунди після кінця викиду, коли рахуємо топ проданих артефактів
    (час на активний збір і продаж)."""

    tg_token: str = os.getenv("TG_TOKEN", "")
    tg_chat_ids: list[str] = field(default_factory=lambda: _split(os.getenv("TG_CHAT_IDS", "")))

    state_path: str = os.getenv("STATE_PATH", "data/state.json")

    @property
    def lock_path(self) -> str:
        """Лок-файл поруч зі станом: один екземпляр на один STATE_PATH."""
        return str(Path(self.state_path).with_suffix(".lock"))

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
