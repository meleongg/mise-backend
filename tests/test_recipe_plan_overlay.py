"""Tests for week-aware recipe overlay from personally bound plan entries."""

from __future__ import annotations

import json
import uuid

from app.models import PersonalRecipe, WeeklyPlanEntry
from app.services.weekly_plan import (
    bind_personal_recipe_to_active_entries,
    ensure_plan_entries,
)


def test_get_recipe_without_week_is_catalog(client, test_recipes):
    recipe = test_recipes[0]
    res = client.get(f"/api/recipe/{recipe.id}")
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == str(recipe.id)
    assert body["name"] == recipe.name
    assert body.get("content_source") in (None, "catalog")
    assert body.get("personal_recipe_id") in (None,)


def test_get_recipe_with_week_overlays_bound_personal_snapshot(
    client, db, test_user, test_recipes, test_plan
):
    recipe = test_recipes[0]
    ensure_plan_entries(test_plan, db)
    db.flush()

    personal = PersonalRecipe(
        id=uuid.uuid4(),
        user_id=test_user.id,
        source_recipe_id=recipe.id,
        name="Spicy Overlay Pasta",
        ingredients=json.dumps(
            [{"name": "pasta", "measure": "200g"}, {"name": "chili", "measure": "1 tsp"}]
        ),
        instructions=json.dumps(
            [{"step": 1, "text": "Boil pasta"}, {"step": 2, "text": "Add chili"}]
        ),
        portion_size=2.0,
        notes="Extra heat",
        metadata_json=json.dumps({"cuisine": "Italian"}),
        current_revision=1,
        is_active=True,
    )
    db.add(personal)
    db.flush()
    bind_personal_recipe_to_active_entries(db, test_user, personal, recipe.id)
    db.commit()

    plain = client.get(f"/api/recipe/{recipe.id}").json()
    assert plain["name"] == recipe.name

    overlaid = client.get(
        f"/api/recipe/{recipe.id}?week_number={test_plan.week_number}"
    )
    assert overlaid.status_code == 200
    body = overlaid.json()
    assert body["id"] == str(recipe.id)  # progress identity stays catalog
    assert body["name"] == "Spicy Overlay Pasta"
    assert body["content_source"] == "plan_entry_personal"
    assert body["personal_recipe_id"] == str(personal.id)
    assert body["plan_entry_id"]
    assert "chili" in body["ingredients"]
    assert "Add chili" in body["instructions"]
    assert body["portion_size"] == 2.0


def test_get_recipe_with_week_falls_back_when_unbound(
    client, db, test_user, test_recipes, test_plan
):
    recipe = test_recipes[0]
    ensure_plan_entries(test_plan, db)
    db.commit()
    entry = (
        db.query(WeeklyPlanEntry)
        .filter(
            WeeklyPlanEntry.weekly_plan_id == test_plan.id,
            WeeklyPlanEntry.catalog_recipe_id == recipe.id,
        )
        .one()
    )
    assert entry.personal_recipe_id is None

    res = client.get(f"/api/recipe/{recipe.id}?week_number={test_plan.week_number}")
    assert res.status_code == 200
    body = res.json()
    assert body["name"] == recipe.name
    assert body.get("content_source") == "catalog"
    assert body.get("personal_recipe_id") is None
