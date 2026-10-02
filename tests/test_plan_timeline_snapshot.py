"""Tests for prep timeline snapshot persistence."""

from __future__ import annotations

import json
import uuid

from app.models import Recipe, User, WeeklyPlan
from app.schemas import WeeklyPlanResponse
from app.services.plan_timeline import build_prep_timeline
from app.services.plan_timeline_attach import (
    attach_prep_timeline,
    parse_stored_timeline,
    rebuild_and_save_prep_timeline,
    save_prep_timeline_on_plan,
    timeline_to_json,
)
from app.services.weekly_plan import create_recipe_schedule


def _user(**overrides) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"snap-{uuid.uuid4()}@example.com",
        first_name="Snap",
        last_name="Shot",
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
        name="Snap Dish",
        cuisine="Italian",
        ingredients=json.dumps([{"name": "pasta", "measure": "200 g"}]),
        instructions="Cook.",
        difficulty="medium",
        dietary_tags=json.dumps(["vegetarian"]),
        allergens=json.dumps([]),
        prep_time_minutes=10,
        cook_time_minutes=20,
    )
    for key, value in overrides.items():
        setattr(recipe, key, value)
    return recipe


def test_timeline_json_round_trip():
    user = _user()
    recipe = _recipe()
    timeline = build_prep_timeline(user, [recipe])
    raw = timeline_to_json(timeline)
    stored = parse_stored_timeline(raw)
    assert stored is not None
    assert stored["kind"] == "deterministic_v1"
    assert "snapshotted_at" in stored
    assert len(stored["items"]) >= 2


def test_attach_prefers_snapshot_over_recompute(db, test_user):
    recipe = _recipe(name="Stored Pasta")
    db.add(recipe)
    db.flush()
    plan = WeeklyPlan(
        id=uuid.uuid4(),
        user_id=test_user.id,
        week_number=1,
        recipe_schedule=create_recipe_schedule([str(recipe.id)]),
        is_unlocked=True,
    )
    timeline = build_prep_timeline(test_user, [recipe], ordered_recipe_ids=[recipe.id])
    # Mutate a distinctive note into the stored snapshot
    payload = json.loads(timeline_to_json(timeline))
    payload["notes"] = ["snapshot-marker-note"]
    plan.prep_timeline_json = json.dumps(payload)
    db.add(plan)
    db.flush()

    response = WeeklyPlanResponse.model_validate(plan)
    response.recipes = []
    # Pass a different in-memory recipe name; snapshot should still win
    other = _recipe(name="Should Not Appear")
    attach_prep_timeline(
        response,
        test_user,
        [other],
        plan=plan,
        prefer_snapshot=True,
    )
    assert response.prep_timeline is not None
    assert response.prep_timeline.source == "snapshot"
    assert "snapshot-marker-note" in response.prep_timeline.notes


def test_attach_computes_and_lazy_persists_when_missing(db, test_user):
    recipe = _recipe()
    db.add(recipe)
    db.flush()
    plan = WeeklyPlan(
        id=uuid.uuid4(),
        user_id=test_user.id,
        week_number=2,
        recipe_schedule=create_recipe_schedule([str(recipe.id)]),
        is_unlocked=True,
        prep_timeline_json=None,
    )
    db.add(plan)
    db.flush()

    response = WeeklyPlanResponse.model_validate(plan)
    attach_prep_timeline(
        response,
        test_user,
        [recipe],
        plan=plan,
        prefer_snapshot=True,
        persist_if_missing=True,
    )
    assert plan.prep_timeline_json
    assert response.prep_timeline is not None
    assert response.prep_timeline.source == "snapshot"
    stored = parse_stored_timeline(plan.prep_timeline_json)
    assert stored is not None
    assert any(i["kind"] == "shop" for i in stored["items"])


def test_rebuild_and_save_updates_snapshot(db, test_user):
    a = _recipe(name="A")
    b = _recipe(name="B")
    db.add_all([a, b])
    db.flush()
    plan = WeeklyPlan(
        id=uuid.uuid4(),
        user_id=test_user.id,
        week_number=3,
        recipe_schedule=create_recipe_schedule([str(a.id)]),
        is_unlocked=True,
    )
    save_prep_timeline_on_plan(
        plan, build_prep_timeline(test_user, [a], ordered_recipe_ids=[a.id])
    )
    first = plan.prep_timeline_json
    plan.recipe_schedule = create_recipe_schedule([str(b.id)])
    rebuild_and_save_prep_timeline(
        plan, test_user, [b], ordered_recipe_ids=[b.id]
    )
    assert plan.prep_timeline_json != first
    stored = parse_stored_timeline(plan.prep_timeline_json)
    assert stored is not None
    cook_titles = [i["title"] for i in stored["items"] if i["kind"] == "cook"]
    assert cook_titles == ["B"]
