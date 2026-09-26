"""Тести каталогу артефактів."""
import json

from app.artifacts import CATALOG, Artifact, load_catalog
from app.constants import ARTIFACT_CATEGORY_EMOJI, ARTIFACT_CATEGORY_LABEL


def test_catalog_is_not_empty():
    assert len(CATALOG) > 0


def test_all_ids_are_unique():
    ids = [artifact.id for artifact in CATALOG]
    assert len(ids) == len(set(ids))


def test_all_categories_have_emoji_and_label():
    categories = {artifact.category for artifact in CATALOG}
    assert categories <= ARTIFACT_CATEGORY_EMOJI.keys()
    assert categories <= ARTIFACT_CATEGORY_LABEL.keys()


def test_no_empty_names():
    assert all(artifact.name.strip() for artifact in CATALOG)


def test_unknown_category_falls_back_gracefully():
    artifact = Artifact(id="x", name="Тест", category="not_a_real_category")
    assert artifact.emoji == "❔"
    assert artifact.category_label == "not_a_real_category"


def test_known_category_maps_correctly():
    artifact = Artifact(id="x", name="Тест", category="gravity")
    assert artifact.emoji == "🌀"
    assert artifact.category_label == "гравітаційний"


def test_load_catalog_from_custom_path(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text(
        json.dumps([{"id": "abc1", "name": "Test Item", "category": "thermal"}]),
        encoding="utf-8",
    )
    catalog = load_catalog(path)
    assert catalog == (Artifact(id="abc1", name="Test Item", category="thermal"),)
