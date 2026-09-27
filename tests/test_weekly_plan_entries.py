import json
import uuid

from app.models import WeeklyPlan, WeeklyPlanEntry
from app.services.weekly_plan import (
    create_recipe_schedule,
    ensure_plan_entries,
    sync_plan_entries_from_schedule,
)


def test_sync_plan_entries_from_schedule_creates_ordered_slots(
    db, test_user, test_recipes
):
    schedule = create_recipe_schedule(
        [str(test_recipes[2].id), str(test_recipes[0].id)]
    )
    plan = WeeklyPlan(
        id=uuid.uuid4(),
        user_id=test_user.id,
        week_number=3,
        recipe_schedule=schedule,
        swap_count=0,
    )
    db.add(plan)
    db.flush()

    entries = sync_plan_entries_from_schedule(plan, db)
    db.flush()

    assert len(entries) == 2
    assert [e.position for e in entries] == [0, 1]
    assert entries[0].catalog_recipe_id == test_recipes[2].id
    assert entries[1].catalog_recipe_id == test_recipes[0].id
    assert entries[0].personal_recipe_id is None
    assert entries[0].lifecycle_state == "planned"
    snapshot = json.loads(entries[0].recipe_snapshot)
    assert snapshot["name"] == test_recipes[2].name


def test_ensure_plan_entries_lazy_backfills_once(db, test_user, test_recipes, test_plan):
    test_plan.recipe_schedule = create_recipe_schedule(
        [str(test_recipes[1].id), str(test_recipes[0].id)]
    )
    db.flush()

    assert (
        db.query(WeeklyPlanEntry)
        .filter(WeeklyPlanEntry.weekly_plan_id == test_plan.id)
        .count()
        == 0
    )

    first = ensure_plan_entries(test_plan, db)
    second = ensure_plan_entries(test_plan, db)
    db.flush()

    assert len(first) == 2
    assert [e.id for e in first] == [e.id for e in second]
    assert [e.catalog_recipe_id for e in first] == [
        test_recipes[1].id,
        test_recipes[0].id,
    ]


def test_sync_replaces_entries_when_schedule_changes(
    db, test_user, test_recipes, test_plan
):
    test_plan.recipe_schedule = create_recipe_schedule([str(test_recipes[0].id)])
    sync_plan_entries_from_schedule(test_plan, db)
    db.flush()
    old_ids = {
        e.id
        for e in db.query(WeeklyPlanEntry)
        .filter(WeeklyPlanEntry.weekly_plan_id == test_plan.id)
        .all()
    }

    test_plan.recipe_schedule = create_recipe_schedule(
        [str(test_recipes[1].id), str(test_recipes[2].id)]
    )
    sync_plan_entries_from_schedule(test_plan, db)
    db.flush()

    entries = (
        db.query(WeeklyPlanEntry)
        .filter(WeeklyPlanEntry.weekly_plan_id == test_plan.id)
        .order_by(WeeklyPlanEntry.position.asc())
        .all()
    )
    assert len(entries) == 2
    assert {e.id for e in entries}.isdisjoint(old_ids)
    assert [e.catalog_recipe_id for e in entries] == [
        test_recipes[1].id,
        test_recipes[2].id,
    ]


def test_get_weekly_plan_returns_entries(client, test_user, test_plan, test_recipes, db):
    test_plan.recipe_schedule = json.dumps(
        [
            {"recipe_id": str(test_recipes[1].id), "order": 0},
            {"recipe_id": str(test_recipes[0].id), "order": 1},
        ]
    )
    db.flush()

    response = client.get(
        f"/api/weekly-plan?user_id={test_user.id}&week_number={test_plan.week_number}"
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["entries"]) == 2
    assert [e["position"] for e in data["entries"]] == [0, 1]
    assert data["entries"][0]["catalog_recipe_id"] == str(test_recipes[1].id)
    assert data["entries"][1]["catalog_recipe_id"] == str(test_recipes[0].id)
    assert isinstance(data["entries"][0]["recipe_snapshot"], dict)
    assert data["entries"][0]["recipe_snapshot"]["name"] == test_recipes[1].name


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
