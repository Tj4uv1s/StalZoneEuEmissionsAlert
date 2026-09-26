"""Каталог артефактів: статичні дані з офіційної бази EXBO-Studio/stalzone-database.

Мережі тут немає — каталог зберігається як файл у репозиторії (artifacts_catalog.json),
зібраний одноразовим переглядом https://github.com/EXBO-Studio/stalzone-database.
API гри не дає списку артефактів напряму — тільки цю зовнішню базу предметів.
Оновлювати вручну, коли в грі з'являються нові артефакти.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.constants import ARTIFACT_CATEGORY_EMOJI, ARTIFACT_CATEGORY_LABEL

_CATALOG_PATH = Path(__file__).parent / "artifacts_catalog.json"


@dataclass(frozen=True)
class Artifact:
    """Один запис каталогу артефактів."""

    id: str
    name: str
    category: str

    @property
    def emoji(self) -> str:
        """Емодзі категорії. '❔', якщо категорія невідома (нова, без оновлення каталогу)."""
        return ARTIFACT_CATEGORY_EMOJI.get(self.category, "❔")

    @property
    def category_label(self) -> str:
        """Підпис категорії українською. Сама назва category, якщо підпису немає."""
        return ARTIFACT_CATEGORY_LABEL.get(self.category, self.category)


def load_catalog(path: Path = _CATALOG_PATH) -> tuple[Artifact, ...]:
    """Завантажити каталог із JSON-файлу.

    Args:
        path: Шлях до файлу каталогу (за замовчуванням — вбудований у пакет)

    Returns:
        Кортеж артефактів
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    return tuple(Artifact(entry["id"], entry["name"], entry["category"]) for entry in raw)


CATALOG: tuple[Artifact, ...] = load_catalog()
