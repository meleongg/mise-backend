"""Tests for deterministic plan evaluator and repair intent helper."""

from __future__ import annotations

import json
import uuid

from app.models import Recipe, User, UserPantryItem
from app.services.plan_evaluator import evaluate_plan_candidates
from app.services.plan_verification import build_repair_intent_suffix


def _user(**overrides) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"eval-{uuid.uuid4()}@example.com",
        first_name="Eval",
        last_name="Tester",
        cuisine="Italian",
        frequency=2,
        skill_level="intermediate",
        user_goal="Learn New Techniques",
        hashed_password="x",
        dietary_restrictions=json.dumps([]),
        allergens=json.dumps([]),
        max_prep_time_minutes=30,
        max_cook_time_minutes=45,
    )
    for key, value in overrides.items():
        setattr(user, key, value)
    return user


def _recipe(**overrides) -> Recipe:
    recipe = Recipe(
        id=uuid.uuid4(),
        external_id=f"ext-{uuid.uuid4()}",
        name="Clear Dish",
        cuisine="Italian",
        ingredients=json.dumps(
            [
                {"name": "pasta", "measure": "200 g"},
                {"name": "olive oil", "measure": "2 tbsp"},
            ]
        ),
        instructions="Cook.",
        difficulty="medium",
        dietary_tags=json.dumps(["vegetarian"]),
        allergens=json.dumps([]),
        prep_time_minutes=10,
        cook_time_minutes=15,
    )
    for key, value in overrides.items():
        setattr(recipe, key, value)
    return recipe


def test_build_repair_intent_suffix_lists_codes_only():
    text = build_repair_intent_suffix(["allergen_conflict", "recipe_count_mismatch"])
    assert "allergen_conflict" in text
    assert "recipe_count_mismatch" in text
    assert "Previous plan failed verification" in text


def test_evaluator_high_confidence_clean_recipes(db, test_user):
    test_user.max_prep_time_minutes = 60
    test_user.max_cook_time_minutes = 60
    test_user.frequency = 2
    db.flush()
    a = _recipe()
    b = _recipe(name="Salad", external_id=f"b-{uuid.uuid4()}")
    db.add_all([a, b])
    db.flush()
    result = evaluate_plan_candidates(db, test_user, [a, b])
    assert result.confidence in {"high", "medium", "low"}
    assert result.evaluator_passed is True
    client = result.as_client_summary(
        verification_run_id="x", attempt_number=1, auto_repaired=False
    )
    assert "recipe_id" not in json.dumps(client)
    assert client["confidence"] == result.confidence


def test_evaluator_flags_ambiguous_measures(db, test_user):
    test_user.max_prep_time_minutes = 60
    test_user.max_cook_time_minutes = 60
    test_user.frequency = 1
    db.flush()
    recipe = _recipe(
        ingredients=json.dumps(
            [
                {"name": "sauce", "measure": "a splash"},
                {"name": "spice", "measure": "to taste"},
                {"name": "herb", "measure": "some"},
                {"name": "stock", "measure": "as needed"},
            ]
        )
    )
    db.add(recipe)
    db.flush()
    result = evaluate_plan_candidates(db, test_user, [recipe])
    assert result.signals["ambiguous_ingredient_count"] >= 3
    assert result.confidence == "low"
    assert any("shopping" in r.lower() for r in result.confidence_reasons)


def test_evaluator_pantry_signal(db, test_user):
    test_user.max_prep_time_minutes = 60
    test_user.max_cook_time_minutes = 60
    test_user.frequency = 1
    db.add(UserPantryItem(id=uuid.uuid4(), user_id=test_user.id, name="pasta"))
    db.flush()
    recipe = _recipe()
    db.add(recipe)
    db.flush()
    result = evaluate_plan_candidates(db, test_user, [recipe])
    assert result.signals["pantry_matched_ingredients"] >= 1
    assert result.signals["pantry_coverage_ratio"] > 0
