"""Тести сховища стану."""
import json

from app.storage import JsonStorage


def test_roundtrip_through_file(tmp_path):
    path = tmp_path / "data" / "state.json"
    JsonStorage(path).set("EU", {"announced_end": "2026-09-28T10:00:00Z"})
    assert JsonStorage(path).get("EU") == {"announced_end": "2026-09-28T10:00:00Z"}


def test_write_is_atomic_and_leaves_no_temp_file(tmp_path):
    path = tmp_path / "state.json"
    storage = JsonStorage(path)
    storage.set("EU", {"a": 1})
    storage.set("RU", {"b": 2})

    assert json.loads(path.read_text(encoding="utf-8")) == {"EU": {"a": 1}, "RU": {"b": 2}}
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_missing_region_is_empty(tmp_path):
    assert JsonStorage(tmp_path / "state.json").get("NA") == {}
