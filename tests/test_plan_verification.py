"""Unit tests for deterministic weekly-plan verification gate."""

from __future__ import annotations

import json
import uuid

from app.models import Recipe, User
from app.services.plan_verification import verify_plan_candidates


def _user(**overrides) -> User:
    user = User(
        id=uuid.uuid4(),
        email="gate@example.com",
        first_name="Gate",
        last_name="Tester",
        cuisine="Italian",
        frequency=2,
        skill_level="intermediate",
        user_goal="Learn New Techniques",
        hashed_password="x",
        dietary_restrictions=json.dumps([]),
        allergens=json.dumps([]),
    )
    for key, value in overrides.items():
        setattr(user, key, value)
    return user


def _recipe(**overrides) -> Recipe:
    recipe = Recipe(
        id=uuid.uuid4(),
        name="Safe Pasta",
        cuisine="Italian",
        ingredients=json.dumps(
            [{"name": "pasta", "measure": "200g"}, {"name": "tomato", "measure": "1"}]
        ),
        instructions="Boil and sauce.",
        difficulty="medium",
        dietary_tags=json.dumps(["vegetarian"]),
        allergens=json.dumps(["gluten"]),
        prep_time_minutes=15,
        cook_time_minutes=20,
    )
    for key, value in overrides.items():
        setattr(recipe, key, value)
    return recipe


def test_happy_path_passes():
    a = _recipe()
    b = _recipe(name="Salad", allergens=json.dumps([]), dietary_tags=json.dumps(["vegetarian"]))
    user = _user(frequency=2)
    result = verify_plan_candidates(user, [a.id, b.id], [a, b])
    assert result.ok is True
    assert result.failures == []


def test_allergen_conflict_hard_fails():
    recipe = _recipe(allergens=json.dumps(["dairy", "gluten"]))
    user = _user(frequency=1, allergens=json.dumps(["dairy"]))
    result = verify_plan_candidates(user, [recipe.id], [recipe])
    assert result.ok is False
    assert any(f.code == "allergen_conflict" for f in result.failures)
    detail = result.as_detail()
    assert detail["code"] == "plan_verification_failed"
    assert "failures" in detail
    # Do not leak recipe names in the top-level message.
    assert "Safe Pasta" not in detail["message"]
    client = result.as_client_detail()
    assert "failures" not in client
    assert "recipe_id" not in json.dumps(client)
    assert "allergen_conflict" in client["failure_codes"]


def test_missing_allergen_metadata_fails_when_user_has_allergens():
    recipe = _recipe(allergens=None)
    user = _user(frequency=1, allergens=json.dumps(["nuts"]))
    result = verify_plan_candidates(user, [recipe.id], [recipe])
    assert result.ok is False
    assert any(f.code == "missing_allergen_metadata" for f in result.failures)


def test_dietary_conflict_hard_fails():
    recipe = _recipe(dietary_tags=json.dumps(["pescatarian"]))
    user = _user(frequency=1, dietary_restrictions=json.dumps(["vegan"]))
    result = verify_plan_candidates(user, [recipe.id], [recipe])
    assert result.ok is False
    assert any(f.code == "dietary_conflict" for f in result.failures)


def test_incomplete_ingredients_fails():
    recipe = _recipe(ingredients=json.dumps([]))
    user = _user(frequency=1)
    result = verify_plan_candidates(user, [recipe.id], [recipe])
    assert result.ok is False
    assert any(f.code == "incomplete_ingredients" for f in result.failures)


def test_recipe_count_mismatch_fails():
    a = _recipe()
    user = _user(frequency=3)
    result = verify_plan_candidates(user, [a.id], [a])
    assert result.ok is False
    assert any(f.code == "recipe_count_mismatch" for f in result.failures)


def test_missing_recipe_id_fails():
    a = _recipe()
    missing = uuid.uuid4()
    user = _user(frequency=2)
    result = verify_plan_candidates(user, [a.id, missing], [a])
    assert result.ok is False
    assert any(f.code == "recipe_missing" for f in result.failures)
