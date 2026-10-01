import json
import uuid
from datetime import datetime, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from app.models import UserPantryItem, WeeklyPlan, WeeklyPlanEntry
from app.services.servings import parse_servings, scale_factor
from app.services.shopping import build_aggregated_items
from app.services.weekly_plan import create_recipe_schedule


def _entry(db, plan, recipe, position, ingredients, *, portion_size=2.0, selected=2.0):
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
                "portion_size": portion_size,
                "ingredients": ingredients,
            }
        ),
        selected_servings=selected,
        lifecycle_state="planned",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(entry)
    db.flush()
    return entry


def test_migration_revises_shopping_head():
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "g5b6c7d8_serving_scale_and_pantry_omit.py"
    )
    spec = spec_from_file_location("g5b6c7d8", path)
    assert spec is not None and spec.loader is not None
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.revision == "g5b6c7d8"
    assert mod.down_revision == "f4a5b6c7"


def test_parse_servings_rejects_ranges_and_family():
    assert parse_servings("4") == 4.0
    assert parse_servings(4) == 4.0
    assert parse_servings(4.0) == 4.0
    assert parse_servings("1/2") == 0.5
    assert parse_servings("3 servings") == 3.0
    assert parse_servings("Serves 4") == 4.0
    assert parse_servings("3-4") is None
    assert parse_servings("6+") is None
    assert parse_servings("family") is None


def test_coerce_servings_for_migration_low_end():
    from app.services.servings import coerce_servings_for_migration

    assert coerce_servings_for_migration("6-8 people") == 6.0
    assert coerce_servings_for_migration("6+") == 6.0
    assert coerce_servings_for_migration("Serves 4") == 4.0
    assert coerce_servings_for_migration("family") is None


def test_migration_numeric_portion_revises_prep_timeline_head():
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "j8e9f0a1_numeric_portion_size.py"
    )
    spec = spec_from_file_location("j8e9f0a1", path)
    assert spec is not None and spec.loader is not None
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.revision == "j8e9f0a1"
    assert mod.down_revision == "i7d8e9f0"


def test_scale_doubles_when_servings_parse(db, test_user, test_recipes):
    plan = WeeklyPlan(
        id=uuid.uuid4(),
        user_id=test_user.id,
        week_number=10,
        recipe_schedule=create_recipe_schedule([str(test_recipes[0].id)]),
        swap_count=0,
    )
    db.add(plan)
    db.flush()
    entry = _entry(
        db,
        plan,
        test_recipes[0],
        0,
        [{"name": "Onion", "measure": "1 cup"}],
        portion_size=2.0,
        selected=4.0,
    )
    items = build_aggregated_items([entry])
    onion = next(i for i in items if i["normalized_name"] == "onion")
    assert onion["quantity"] == 2.0
    assert onion["unit"] == "cup"
    assert onion["needs_review"] is False


def test_unparseable_servings_leave_qty_and_flag_review(db, test_user, test_recipes):
    plan = WeeklyPlan(
        id=uuid.uuid4(),
        user_id=test_user.id,
        week_number=11,
        recipe_schedule=create_recipe_schedule([str(test_recipes[0].id)]),
        swap_count=0,
    )
    db.add(plan)
    db.flush()
    entry = _entry(
        db,
        plan,
        test_recipes[0],
        0,
        [{"name": "Onion", "measure": "1 cup"}],
        portion_size="family",
        selected=2.0,
    )
    items = build_aggregated_items([entry])
    onion = next(i for i in items if i["normalized_name"] == "onion")
    assert onion["quantity"] == 1.0
    assert onion["needs_review"] is True
    assert "servings not scaled" in (onion["reason"] or "")


def test_patch_selected_servings_and_generate_scales(
    client, db, test_user, test_recipes, test_plan
):
    test_plan.recipe_schedule = create_recipe_schedule([str(test_recipes[0].id)])
    db.flush()
    entry = _entry(
        db,
        test_plan,
        test_recipes[0],
        0,
        [{"name": "Rice", "measure": "1 cup"}],
        portion_size=2.0,
        selected=2.0,
    )
    original_snapshot = entry.recipe_snapshot

    patched = client.patch(
        f"/api/weekly-plan/entries/{entry.id}",
        json={"selected_servings": 4},
    )
    assert patched.status_code == 200
    assert patched.json()["selected_servings"] == 4
    db.refresh(entry)
    assert entry.recipe_snapshot == original_snapshot

    generated = client.post(
        "/api/shopping-lists/generate",
        json={"week_number": test_plan.week_number},
    )
    assert generated.status_code == 200
    rice = next(i for i in generated.json()["items"] if i["normalized_name"] == "rice")
    assert rice["quantity"] == 2.0


def test_omit_requires_confirm_and_survives_refresh(
    client, db, test_user, test_recipes, test_plan
):
    test_plan.recipe_schedule = create_recipe_schedule([str(test_recipes[0].id)])
    db.flush()
    _entry(
        db,
        test_plan,
        test_recipes[0],
        0,
        [{"name": "Garlic", "measure": "2 cloves"}],
        portion_size=2.0,
        selected=2.0,
    )
    created = client.post(
        "/api/shopping-lists/generate",
        json={"week_number": test_plan.week_number},
    )
    assert created.status_code == 200
    garlic = next(i for i in created.json()["items"] if i["normalized_name"] == "garlic")

    denied = client.patch(
        f"/api/shopping-lists/items/{garlic['id']}",
        json={"omitted_by_pantry": True},
    )
    assert denied.status_code == 400

    omitted = client.patch(
        f"/api/shopping-lists/items/{garlic['id']}",
        json={"omitted_by_pantry": True, "confirm_pantry_omit": True},
    )
    assert omitted.status_code == 200
    garlic2 = next(i for i in omitted.json()["items"] if i["normalized_name"] == "garlic")
    assert garlic2["omitted_by_pantry"] is True
    assert garlic2["pantry_omit_confirmed_at"] is not None

    refreshed = client.post(
        "/api/shopping-lists/generate",
        json={"week_number": test_plan.week_number},
    )
    assert refreshed.status_code == 200
    garlic3 = next(
        i for i in refreshed.json()["items"] if i["normalized_name"] == "garlic"
    )
    assert garlic3["omitted_by_pantry"] is True


def test_baseline_pantry_does_not_auto_omit(
    client, db, test_user, test_recipes, test_plan
):
    db.add(
        UserPantryItem(
            id=uuid.uuid4(),
            user_id=test_user.id,
            name="Garlic",
            is_baseline=True,
        )
    )
    test_plan.recipe_schedule = create_recipe_schedule([str(test_recipes[0].id)])
    db.flush()
    _entry(
        db,
        test_plan,
        test_recipes[0],
        0,
        [{"name": "Garlic", "measure": "2 cloves"}],
    )
    created = client.post(
        "/api/shopping-lists/generate",
        json={"week_number": test_plan.week_number},
    )
    assert created.status_code == 200
    garlic = next(i for i in created.json()["items"] if i["normalized_name"] == "garlic")
    assert garlic["omitted_by_pantry"] is False
    assert garlic["pantry_match"] is True


def test_scale_factor_helpers():
    assert scale_factor("4", "2") == (2.0, False, None)
    factor, review, reason = scale_factor("family", "2")
    assert factor == 1.0
    assert review is True
    assert reason is not None
