"""Tests for deterministic prep timeline and recipe explanations."""

from __future__ import annotations

import json
import uuid

from app.models import Recipe, User
from app.services.plan_timeline import (
    ADVANCE_PREP_MINUTES,
    build_prep_timeline,
    recipe_explanations,
)


def _user(**overrides) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"timeline-{uuid.uuid4()}@example.com",
        first_name="Time",
        last_name="Line",
        cuisine="Italian",
        frequency=2,
        skill_level="intermediate",
        user_goal="Learn New Techniques",
        hashed_password="x",
        dietary_restrictions=json.dumps(["vegetarian"]),
        allergens=json.dumps([]),
        max_prep_time_minutes=30,
        max_cook_time_minutes=45,
        city="Austin",
        preferred_retailer="HEB",
    )
    for key, value in overrides.items():
        setattr(user, key, value)
    return user


def _recipe(**overrides) -> Recipe:
    recipe = Recipe(
        id=uuid.uuid4(),
        external_id=f"ext-{uuid.uuid4()}",
        name="Pasta Night",
        cuisine="Italian",
        ingredients=json.dumps([{"name": "pasta", "measure": "200 g"}]),
        instructions="Cook.",
        difficulty="medium",
        dietary_tags=json.dumps(["vegetarian"]),
        allergens=json.dumps([]),
        prep_time_minutes=10,
        cook_time_minutes=20,
        skill_level_validated="intermediate",
    )
    for key, value in overrides.items():
        setattr(recipe, key, value)
    return recipe


def test_timeline_orders_shop_then_cook_days():
    user = _user()
    a = _recipe(name="A")
    b = _recipe(name="B", external_id=f"b-{uuid.uuid4()}")
    timeline = build_prep_timeline(user, [a, b], ordered_recipe_ids=[a.id, b.id])
    kinds = [item.kind for item in timeline.items]
    assert kinds[0] == "shop"
    assert kinds.count("cook") == 2
    assert timeline.items[1].day_label == "Day 1"
    assert timeline.items[2].day_label == "Day 2"
    assert "HEB" in timeline.items[0].detail
    assert "Austin" in timeline.items[0].detail


def test_advance_prep_when_prep_meets_threshold():
    user = _user()
    long_prep = _recipe(
        name="Slow Prep",
        prep_time_minutes=ADVANCE_PREP_MINUTES,
        cook_time_minutes=15,
    )
    timeline = build_prep_timeline(user, [long_prep])
    kinds = [item.kind for item in timeline.items]
    assert kinds == ["shop", "advance_prep", "cook"]
    advance = timeline.items[1]
    assert advance.duration_minutes == ADVANCE_PREP_MINUTES
    assert advance.recipe_id == str(long_prep.id)
    cook = timeline.items[2]
    assert cook.duration_minutes == 15
    assert cook.day_label == "Day 1"


def test_missing_time_adds_note_and_zero_duration_cook():
    user = _user()
    mystery = _recipe(prep_time_minutes=None, cook_time_minutes=None)
    timeline = build_prep_timeline(user, [mystery])
    assert timeline.total_active_minutes == 0
    assert any("missing prep/cook" in n.lower() for n in timeline.notes)
    cook = next(i for i in timeline.items if i.kind == "cook")
    assert cook.duration_minutes is None
    assert "Timing not listed" in cook.detail


def test_recipe_explanations_cuisine_and_time():
    user = _user()
    recipe = _recipe()
    reasons = recipe_explanations(user, recipe)
    joined = " ".join(reasons).lower()
    assert "italian" in joined
    assert "prep" in joined or "cook" in joined
    assert "dietary" in joined or "skill" in joined or "italian" in joined


def test_ordered_recipe_ids_control_day_labels():
    user = _user()
    first = _recipe(name="First")
    second = _recipe(name="Second", external_id=f"s-{uuid.uuid4()}")
    # Pass recipes in reverse physical order but ordered ids first→second
    timeline = build_prep_timeline(
        user, [second, first], ordered_recipe_ids=[first.id, second.id]
    )
    cooks = [i for i in timeline.items if i.kind == "cook"]
    assert [c.title for c in cooks] == ["First", "Second"]
