"""Tests for hybrid search filter helpers."""

from app.utils.recipe_search_filters import (
    allergen_filter_clause,
    dietary_filter_clause,
    enrich_intent_query,
    normalize_preference_tags,
)
from app.utils.recipe_search_text import build_recipe_content_text


def test_normalize_preference_tags_dedupes_and_lowercases():
    assert normalize_preference_tags(["Nuts", "dairy", "nuts"]) == ["dairy", "nuts"]


def test_enrich_intent_query_includes_allergens_and_dietary():
    q = enrich_intent_query(
        "weeknight pasta",
        cuisine="Italian",
        dietary_restrictions=["Vegetarian"],
        allergens=["Shellfish"],
        skill_level="beginner",
        max_prep_time=30,
    )
    assert "Italian" in q
    assert "vegetarian" in q
    assert "shellfish" in q
    assert "prep under 30" in q


def test_dietary_filter_requires_json_containment():
    sql, params = dietary_filter_clause(["vegetarian", "gluten-free"])
    assert "dietary_tags::jsonb @>" in sql
    assert "vegetarian" in params["dietary_required"]


def test_allergen_filter_excludes_overlap():
    sql, params = allergen_filter_clause(["dairy", "nuts"])
    assert "NOT" in sql
    assert "&&" in sql
    assert "dairy" in params["avoid_allergens"]


def test_build_recipe_content_text_includes_metadata():
    text = build_recipe_content_text(
        name="Pasta",
        cuisine="Italian",
        dietary_tags=["vegetarian"],
        allergens=["wheat"],
        portion_size="4 servings",
        prep_time_minutes=10,
        cook_time_minutes=20,
        skill_level_validated="beginner",
        difficulty="easy",
        ingredients_text="pasta flour",
        instructions_text="boil",
    )
    assert "vegetarian" in text
    assert "wheat" in text
    assert "prep 10 minutes" in text
    assert "portion 4 servings" in text
