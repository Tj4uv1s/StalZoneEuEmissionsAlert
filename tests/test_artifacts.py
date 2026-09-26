"""Тести каталогу артефактів."""
import json
import re

from app.artifacts import CATALOG, Artifact, load_catalog


def test_catalog_is_not_empty():
    assert len(CATALOG) > 0


def test_all_ids_are_unique():
    ids = [artifact.id for artifact in CATALOG]
    assert len(ids) == len(set(ids))


def test_no_empty_names():
    assert all(artifact.name.strip() for artifact in CATALOG)


def test_names_are_russian():
    """Українського перекладу в грі немає — беремо російський, не англійський."""
    assert all(re.search("[А-Яа-яЁё]", artifact.name) for artifact in CATALOG)


def test_load_catalog_from_custom_path(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text(
        json.dumps([{"id": "abc1", "name": "Пустышка", "category": "thermal"}]),
        encoding="utf-8",
    )
    catalog = load_catalog(path)
    assert catalog == (Artifact(id="abc1", name="Пустышка", category="thermal"),)
