import json
import uuid
from datetime import datetime, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from app.models import WeeklyPlan, WeeklyPlanEntry
from app.services.shopping import (
    build_aggregated_items,
)
from app.services.weekly_plan import create_recipe_schedule


def _entry(db, plan, recipe, position, ingredients):
    entry = WeeklyPlanEntry(
        id=uuid.uuid4(),
        weekly_plan_id=plan.id,
        position=position,
        catalog_recipe_id=recipe.id,
        personal_recipe_id=None,
        recipe_snapshot=json.dumps(
            {
                "id": str(recipe.id),
                "name": recipe.name,
                "ingredients": ingredients,
            }
        ),
        selected_servings="2",
        lifecycle_state="planned",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(entry)
    db.flush()
    return entry


def test_shopping_migration_revises_plan_entries_head():
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "f4a5b6c7_add_shopping_lists.py"
    )
    spec = spec_from_file_location("f4a5b6c7_add_shopping_lists", path)
    assert spec is not None and spec.loader is not None
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.revision == "f4a5b6c7"
    assert mod.down_revision == "e3f4a5b6"


def test_aggregate_sums_shared_onion_across_entries(db, test_user, test_recipes):
    plan = WeeklyPlan(
        id=uuid.uuid4(),
        user_id=test_user.id,
        week_number=4,
        recipe_schedule=create_recipe_schedule(
            [str(test_recipes[0].id), str(test_recipes[1].id)]
        ),
        swap_count=0,
    )
    db.add(plan)
    db.flush()
    e1 = _entry(
        db,
        plan,
        test_recipes[0],
        0,
        [{"name": "Onion", "measure": "1 cup"}, {"name": "Salt", "measure": "1 tsp"}],
    )
    e2 = _entry(
        db,
        plan,
        test_recipes[1],
        1,
        [{"name": "onion", "measure": "2 cup"}, {"name": "Garlic", "measure": "2 cloves"}],
    )

    items = build_aggregated_items([e1, e2])
    onion = next(i for i in items if i["normalized_name"] == "onion")
    assert onion["quantity"] == 3.0
    assert onion["unit"] == "cup"
    assert len(onion["sources"]) == 2
    assert {s["weekly_plan_entry_id"] for s in onion["sources"]} == {e1.id, e2.id}


def test_ambiguous_units_stay_separate(db, test_user, test_recipes):
    plan = WeeklyPlan(
        id=uuid.uuid4(),
        user_id=test_user.id,
        week_number=5,
        recipe_schedule=create_recipe_schedule(
            [str(test_recipes[0].id), str(test_recipes[1].id)]
        ),
        swap_count=0,
    )
    db.add(plan)
    db.flush()
    e1 = _entry(
        db,
        plan,
        test_recipes[0],
        0,
        [{"name": "Butter", "measure": "2 tbsp"}],
    )
    e2 = _entry(
        db,
        plan,
        test_recipes[1],
        1,
        [{"name": "Butter", "measure": "50 g"}],
    )
    items = build_aggregated_items([e1, e2])
    butter_rows = [i for i in items if i["normalized_name"] == "butter"]
    assert len(butter_rows) == 2
    assert all(i["needs_review"] for i in butter_rows)


def test_generate_preserves_checked_and_manual_edits(
    client, db, test_user, test_recipes, test_plan
):
    test_plan.recipe_schedule = create_recipe_schedule(
        [str(test_recipes[0].id), str(test_recipes[1].id)]
    )
    db.flush()
    _entry(
        db,
        test_plan,
        test_recipes[0],
        0,
        [{"name": "Onion", "measure": "1 cup"}],
    )
    _entry(
        db,
        test_plan,
        test_recipes[1],
        1,
        [{"name": "Onion", "measure": "1 cup"}, {"name": "Rice", "measure": "1 cup"}],
    )

    first = client.post(
        "/api/shopping-lists/generate",
        json={"week_number": test_plan.week_number},
    )
    assert first.status_code == 200
    data = first.json()
    onion = next(i for i in data["items"] if i["normalized_name"] == "onion")
    assert onion["quantity"] == 2.0
    assert len(onion["sources"]) == 2

    patched = client.patch(
        f"/api/shopping-lists/items/{onion['id']}",
        json={"is_checked": True, "display_text": "Yellow onions (bag)"},
    )
    assert patched.status_code == 200
    onion2 = next(
        i for i in patched.json()["items"] if i["normalized_name"] == "onion"
    )
    assert onion2["is_checked"] is True
    assert onion2["is_user_edit"] is True
    assert onion2["display_text"] == "Yellow onions (bag)"

    refreshed = client.post(
        "/api/shopping-lists/generate",
        json={"week_number": test_plan.week_number},
    )
    assert refreshed.status_code == 200
    onion3 = next(
        i for i in refreshed.json()["items"] if i["normalized_name"] == "onion"
    )
    assert onion3["is_checked"] is True
    assert onion3["display_text"] == "Yellow onions (bag)"
    assert onion3["is_user_edit"] is True


def test_get_active_shopping_list_auth_isolation(client, db, test_user, test_recipes, test_plan):
    test_plan.recipe_schedule = create_recipe_schedule([str(test_recipes[0].id)])
    db.flush()
    _entry(
        db,
        test_plan,
        test_recipes[0],
        0,
        [{"name": "Milk", "measure": "1 cup"}],
    )
    created = client.post(
        "/api/shopping-lists/generate",
        json={"week_number": test_plan.week_number},
    )
    assert created.status_code == 200

    response = client.get(
        f"/api/shopping-lists/active?week_number={test_plan.week_number}"
    )
    assert response.status_code == 200
    assert response.json()["id"] == created.json()["id"]
    assert len(response.json()["items"]) >= 1


def test_omit_entry_drops_unique_ingredient_keeps_shared(
    db, test_user, test_recipes
):
    plan = WeeklyPlan(
        id=uuid.uuid4(),
        user_id=test_user.id,
        week_number=6,
        recipe_schedule=create_recipe_schedule(
            [str(test_recipes[0].id), str(test_recipes[1].id)]
        ),
        swap_count=0,
    )
    db.add(plan)
    db.flush()
    e1 = _entry(
        db,
        plan,
        test_recipes[0],
        0,
        [{"name": "Onion", "measure": "1 cup"}, {"name": "Basil", "measure": "1 bunch"}],
    )
    e2 = _entry(
        db,
        plan,
        test_recipes[1],
        1,
        [{"name": "Onion", "measure": "1 cup"}],
    )
    e2.lifecycle_state = "omitted"
    db.flush()

    items = build_aggregated_items([e1, e2])
    names = {i["normalized_name"] for i in items}
    assert "onion" in names
    assert "basil" in names
    onion = next(i for i in items if i["normalized_name"] == "onion")
    assert onion["quantity"] == 1.0
    assert len(onion["sources"]) == 1


def test_sync_checks_last_client_timestamp_wins(
    client, db, test_user, test_recipes, test_plan
):
    test_plan.recipe_schedule = create_recipe_schedule([str(test_recipes[0].id)])
    db.flush()
    _entry(
        db,
        test_plan,
        test_recipes[0],
        0,
        [{"name": "Milk", "measure": "1 cup"}, {"name": "Eggs", "measure": "6"}],
    )
    created = client.post(
        "/api/shopping-lists/generate",
        json={"week_number": test_plan.week_number},
    )
    assert created.status_code == 200
    items = created.json()["items"]
    milk = next(i for i in items if i["normalized_name"] == "milk")
    eggs = next(i for i in items if i["normalized_name"] == "eggs")

    t1 = "2026-09-30T12:00:00+00:00"
    t2 = "2026-09-30T12:05:00+00:00"
    t3 = "2026-09-30T12:01:00+00:00"  # stale relative to t2 for milk

    synced = client.post(
        "/api/shopping-lists/sync-checks",
        json={
            "updates": [
                {
                    "item_id": milk["id"],
                    "is_checked": True,
                    "client_updated_at": t1,
                },
                {
                    "item_id": milk["id"],
                    "is_checked": False,
                    "client_updated_at": t2,
                },
                {
                    "item_id": milk["id"],
                    "is_checked": True,
                    "client_updated_at": t3,
                },
                {
                    "item_id": eggs["id"],
                    "is_checked": True,
                    "client_updated_at": t2,
                },
                {
                    "item_id": str(uuid.uuid4()),
                    "is_checked": True,
                    "client_updated_at": t2,
                },
            ]
        },
    )
    assert synced.status_code == 200
    body = synced.json()
    milk2 = next(i for i in body["items"] if i["normalized_name"] == "milk")
    eggs2 = next(i for i in body["items"] if i["normalized_name"] == "eggs")
    assert milk2["is_checked"] is False
    assert eggs2["is_checked"] is True
