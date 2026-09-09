"""Простейшее хранилище состояния в JSON-файле.

Ключ — регион, значение — словарь из tracker.diff. Когда появятся подписчики
с разными регионами, этот модуль заменим на SQLite, а остальной код не тронем.
"""
from __future__ import annotations

import json
from pathlib import Path


class JsonStorage:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            self._data = json.loads(self._path.read_text(encoding="utf-8"))

    def get(self, region: str) -> dict:
        return self._data.get(region, {})

    def set(self, region: str, state: dict) -> None:
        self._data[region] = state
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
