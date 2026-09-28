"""Простейше сховище стану у JSON-файлі.

Ключ — регіон, значення — словник з tracker.diff. Коли з'являться підписники
з різними регіонами, цей модуль замінимо на SQLite, але інший код не торкнемо.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


class JsonStorage:
    """Сховище стану в JSON."""

    def __init__(self, path: str | Path) -> None:
        """Ініціалізація сховища.

        Args:
            path: Шлях до JSON-файлу для зберігання стану
        """
        self._path = Path(path)
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        """Завантажити стан із файлу, якщо він існує."""
        if self._path.exists():
            self._data = json.loads(self._path.read_text(encoding="utf-8"))

    def get(self, region: str) -> dict:
        """Отримати стан регіону.

        Args:
            region: Код регіону

        Returns:
            Словник стану (порожній, якщо стану немає)
        """
        return self._data.get(region, {})

    def set(self, region: str, state: dict) -> None:
        """Зберегти стан регіону.

        Args:
            region: Код регіону
            state: Словник стану
        """
        self._data[region] = state
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Атомарно: пишемо в тимчасовий файл і підміняємо. Інакше ручна
        # команда (app.cli), що читає стан поруч із ботом, могла б прочитати
        # напівзаписаний JSON, а падіння бота посеред запису зіпсувало б стан.
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)
