"""Tests for Pexels recipe image resolution."""

import json
import uuid
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.models import Recipe
from app.services import recipe_image as ri


@pytest.fixture
def sample_recipe():
    return Recipe(
        id=uuid.uuid4(),
        name="Pad Thai",
        cuisine="Thai",
        ingredients='[{"name": "rice noodles", "measure": "8 oz"}]',
        instructions="[]",
        difficulty="medium",
        external_id="test-1",
    )


def test_build_search_queries_order(sample_recipe):
    queries = ri.build_search_queries(sample_recipe)
    assert queries[0] == "Pad Thai food"
    assert "Pad Thai Thai" in queries[1]
    assert any("noodles" in q or "rice" in q for q in queries)


def test_searchable_recipe_name_strips_marketing():
    assert ri.searchable_recipe_name("Beginner's Mastery Fried Rice") == "Fried Rice"
    assert ri.searchable_recipe_name("Easy Homestyle Tomato Soup") == "Tomato Soup"


def test_main_ingredient_keywords_skips_pantry():
    ingredients = json.dumps(
        [
            {"name": "Salt", "measure": "1 tsp"},
            {"name": "Chicken breast", "measure": "2"},
            {"name": "Broccoli florets", "measure": "1 cup"},
        ]
    )
    keywords = ri.main_ingredient_keywords(ingredients, max_count=2)
    assert keywords == ["chicken", "broccoli"]


def test_build_queries_uses_ingredients(sample_recipe):
    sample_recipe.ingredients = json.dumps(
        [
            {"name": "rice noodles", "measure": "8 oz"},
            {"name": "shrimp", "measure": "12"},
        ]
    )
    queries = ri.build_search_queries(sample_recipe)
    assert any("shrimp" in q for q in queries)
    assert any("noodles" in q for q in queries)


def test_pexels_disabled_without_key(monkeypatch, sample_recipe):
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    monkeypatch.setenv("PEXELS_API_ENABLED", "true")
    assert ri.resolve_recipe_image(sample_recipe) is None


def test_pick_photo_prefers_alt_match():
    photos = [
        {
            "id": 1,
            "alt": "random salad bowl",
            "src": {
                "large": "https://images.pexels.com/photos/1/large.jpg",
            },
        },
        {
            "id": 2,
            "alt": "delicious pad thai noodles",
            "src": {
                "large": "https://images.pexels.com/photos/2/large.jpg",
            },
        },
    ]
    url = ri._pick_photo_url(photos, "Pad Thai")
    assert url == "https://images.pexels.com/photos/2/large.jpg"


def test_rejects_non_pexels_host():
    photos = [
        {
            "id": 1,
            "alt": "food",
            "src": {"large": "https://evil.example/photo.jpg"},
        },
    ]
    assert ri._pick_photo_url(photos, "Test") is None


@patch("app.services.recipe_image._search_pexels")
def test_resolve_tries_fallback_query(mock_search, monkeypatch, sample_recipe):
    monkeypatch.setenv("PEXELS_API_KEY", "test-key")
    monkeypatch.setenv("PEXELS_API_ENABLED", "true")

    mock_search.side_effect = [
        [],
        [
            {
                "id": 9,
                "alt": "thai food plate",
                "photographer": "Chef",
                "src": {
                    "large": "https://images.pexels.com/photos/9/large.jpg",
                },
            },
        ],
    ]

    url = ri.resolve_recipe_image(sample_recipe)
    assert url == "https://images.pexels.com/photos/9/large.jpg"
    assert mock_search.call_count == 2


@patch("app.services.recipe_image.resolve_recipe_image")
def test_attach_image_if_missing_updates_row(mock_resolve):
    mock_resolve.return_value = "https://images.pexels.com/photos/1/large.jpg"
    recipe = Recipe(
        id=uuid.uuid4(),
        name="Test",
        cuisine="Italian",
        ingredients="[]",
        instructions="[]",
        difficulty="easy",
        external_id="x",
        image_url=None,
    )
    db = MagicMock()
    url = ri.attach_image_if_missing(recipe, db)
    assert url == recipe.image_url
    db.commit.assert_called_once()


@patch("app.services.recipe_image.resolve_recipe_image")
def test_attach_recipe_image_force_overwrites(mock_resolve):
    old = "https://images.pexels.com/photos/old/large.jpg"
    new = "https://images.pexels.com/photos/new/large.jpg"
    mock_resolve.return_value = new
    recipe = Recipe(
        id=uuid.uuid4(),
        name="Fried Rice",
        cuisine="Chinese",
        ingredients="[]",
        instructions="[]",
        difficulty="easy",
        external_id="x",
        image_url=old,
    )
    db = MagicMock()
    url = ri.attach_recipe_image(recipe, db, force=True)
    assert url == new
    assert recipe.image_url == new
    mock_resolve.assert_called_once()


@patch("app.services.recipe_image.resolve_recipe_image")
def test_attach_recipe_image_force_keeps_old_on_failure(mock_resolve):
    old = "https://images.pexels.com/photos/old/large.jpg"
    mock_resolve.return_value = None
    recipe = Recipe(
        id=uuid.uuid4(),
        name="Fried Rice",
        cuisine="Chinese",
        ingredients="[]",
        instructions="[]",
        difficulty="easy",
        external_id="x",
        image_url=old,
    )
    db = MagicMock()
    url = ri.attach_recipe_image(recipe, db, force=True)
    assert url == old
    db.commit.assert_not_called()


@patch("app.services.recipe_image._search_pexels")
def test_resolve_handles_http_error(mock_search, monkeypatch, sample_recipe):
    monkeypatch.setenv("PEXELS_API_KEY", "test-key")
    mock_search.side_effect = httpx.HTTPError("rate limited")
    assert ri.resolve_recipe_image(sample_recipe) is None
