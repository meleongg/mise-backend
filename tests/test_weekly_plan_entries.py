import json
import uuid

from app.models import WeeklyPlan, WeeklyPlanEntry
from app.services.weekly_plan import (
    ensure_plan_entries,
    list_plan_entries,
    ordered_catalog_ids_from_entries,
    replace_plan_catalog_entries,
)


def test_replace_plan_catalog_entries_creates_ordered_slots(
    db, test_user, test_recipes
):
    plan = WeeklyPlan(
        id=uuid.uuid4(),
        user_id=test_user.id,
        week_number=3,
        swap_count=0,
    )
    db.add(plan)
    db.flush()

    entries = replace_plan_catalog_entries(
        plan, [test_recipes[2], test_recipes[0]], db
    )
    db.flush()

    assert len(entries) == 2
    assert [e.position for e in entries] == [0, 1]
    assert entries[0].catalog_recipe_id == test_recipes[2].id
    assert entries[1].catalog_recipe_id == test_recipes[0].id
    assert entries[0].personal_recipe_id is None
    assert entries[0].lifecycle_state == "planned"
    snapshot = json.loads(entries[0].recipe_snapshot)
    assert snapshot["name"] == test_recipes[2].name


def test_ensure_plan_entries_returns_existing(db, test_user, test_recipes, test_plan):
    first = ensure_plan_entries(test_plan, db)
    second = ensure_plan_entries(test_plan, db)
    db.flush()

    assert len(first) == 1
    assert [e.id for e in first] == [e.id for e in second]
    assert first[0].catalog_recipe_id == test_recipes[0].id


def test_replace_plan_catalog_entries_replaces_slots(
    db, test_user, test_recipes, test_plan
):
    old_ids = {e.id for e in list_plan_entries(test_plan, db)}

    entries = replace_plan_catalog_entries(
        test_plan, [test_recipes[1], test_recipes[2]], db
    )
    db.flush()

    assert len(entries) == 2
    assert {e.id for e in entries}.isdisjoint(old_ids)
    assert [e.catalog_recipe_id for e in entries] == [
        test_recipes[1].id,
        test_recipes[2].id,
    ]


def test_get_weekly_plan_returns_entries(client, test_user, test_plan, test_recipes, db):
    replace_plan_catalog_entries(test_plan, [test_recipes[1], test_recipes[0]], db)
    db.commit()

    response = client.get(
        f"/api/weekly-plan?user_id={test_user.id}&week_number={test_plan.week_number}"
    )
    assert response.status_code == 200
    data = response.json()
    assert "recipe_schedule" not in data
    assert len(data["entries"]) == 2
    assert [e["position"] for e in data["entries"]] == [0, 1]
    assert data["entries"][0]["catalog_recipe_id"] == str(test_recipes[1].id)
    assert data["entries"][1]["catalog_recipe_id"] == str(test_recipes[0].id)
    assert isinstance(data["entries"][0]["recipe_snapshot"], dict)
    assert data["entries"][0]["recipe_snapshot"]["name"] == test_recipes[1].name
    assert ordered_catalog_ids_from_entries(list_plan_entries(test_plan, db)) == [
        str(test_recipes[1].id),
        str(test_recipes[0].id),
    ]


def test_weekly_plan_entries_migration_revises_personal_recipes_head():
    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "e3f4a5b6_add_weekly_plan_entries.py"
    )
    spec = spec_from_file_location("e3f4a5b6_add_weekly_plan_entries", path)
    assert spec is not None and spec.loader is not None
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.revision == "e3f4a5b6"
    assert mod.down_revision == "d1e2f3a4"


def test_entries_sole_source_migration_revises_legacy_trim_head():
    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "m1b2c3d4_entries_sole_source.py"
    )
    spec = spec_from_file_location("m1b2c3d4_entries_sole_source", path)
    assert spec is not None and spec.loader is not None
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.revision == "m1b2c3d4"
    assert mod.down_revision == "l0a1b2c3"
