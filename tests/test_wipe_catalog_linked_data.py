"""FK-safe wipe of catalog recipes and dependent demo data."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from app.models import (
    PersonalRecipe,
    PersonalRecipeRevision,
    Recipe,
    RecipeSuggestion,
    ShoppingList,
    ShoppingListItem,
    ShoppingListItemSource,
    UserPantryItem,
    UserRecipeProgress,
    WeeklyPlan,
    WeeklyPlanEntry,
)
from app.services.weekly_plan import replace_plan_catalog_entries
def _load_wipe_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "wipe_catalog_linked_data.py"
    )
    spec = spec_from_file_location("wipe_catalog_linked_data", path)
    assert spec is not None and spec.loader is not None
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_wipe_removes_catalog_linked_rows_keeps_user_and_pantry(
    db, test_user, test_recipes
):
    wipe = _load_wipe_module()

    recipe = test_recipes[0]
    plan = WeeklyPlan(
        id=uuid.uuid4(),
        user_id=test_user.id,
        week_number=99,
        swap_count=0,
    )
    db.add(plan)
    db.flush()

    personal = PersonalRecipe(
        id=uuid.uuid4(),
        user_id=test_user.id,
        source_recipe_id=recipe.id,
        name="Personal copy",
        ingredients=json.dumps([{"name": "Salt", "measure": "1 tsp"}]),
        instructions="Mix",
        portion_size=2.0,
        current_revision=1,
        is_active=True,
    )
    db.add(personal)
    db.flush()
    db.add(
        PersonalRecipeRevision(
            id=uuid.uuid4(),
            personal_recipe_id=personal.id,
            revision_number=1,
            content_snapshot=json.dumps({"name": personal.name}),
            actor_user_id=test_user.id,
        )
    )
    db.flush()

    entry = WeeklyPlanEntry(
        id=uuid.uuid4(),
        weekly_plan_id=plan.id,
        position=0,
        catalog_recipe_id=recipe.id,
        personal_recipe_id=personal.id,
        recipe_snapshot=json.dumps(
            {"id": str(recipe.id), "name": recipe.name, "portion_size": 2}
        ),
        selected_servings=2.0,
        lifecycle_state="planned",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(entry)
    db.flush()

    shopping = ShoppingList(
        id=uuid.uuid4(),
        user_id=test_user.id,
        weekly_plan_id=plan.id,
        title="Week 99",
        status="active",
    )
    db.add(shopping)
    db.flush()
    item = ShoppingListItem(
        id=uuid.uuid4(),
        shopping_list_id=shopping.id,
        normalized_name="salt",
        display_text="1 tsp Salt",
        sort_order=0,
    )
    db.add(item)
    db.flush()
    db.add(
        ShoppingListItemSource(
            id=uuid.uuid4(),
            shopping_list_item_id=item.id,
            weekly_plan_entry_id=entry.id,
            inclusion_state="included",
        )
    )
    db.add(
        UserRecipeProgress(
            id=uuid.uuid4(),
            user_id=test_user.id,
            recipe_id=recipe.id,
            week_number=99,
            status="completed",
        )
    )
    db.add(
        RecipeSuggestion(
            id=uuid.uuid4(),
            user_id=test_user.id,
            recipe_id=recipe.id,
            week_number=99,
            source="plan",
        )
    )
    db.add(
        UserPantryItem(
            id=uuid.uuid4(),
            user_id=test_user.id,
            name="Olive oil",
        )
    )
    db.flush()

    before = wipe.table_counts(db)
    assert before["recipes"] >= 1
    assert before["weekly_plan_entries"] == 1
    assert before["personal_recipes"] == 1

    wipe.wipe_catalog_linked_data(db, dry_run=False, commit=False)

    after = wipe.table_counts(db)
    assert all(v == 0 for v in after.values())
    from app.models import User

    assert db.query(User).filter(User.id == test_user.id).count() == 1
    assert db.query(UserPantryItem).filter(UserPantryItem.user_id == test_user.id).count() == 1
    assert db.query(Recipe).count() == 0


def test_wipe_dry_run_does_not_delete(db, test_recipes):
    wipe = _load_wipe_module()
    before = wipe.table_counts(db)
    assert before["recipes"] >= 1
    returned = wipe.wipe_catalog_linked_data(db, dry_run=True, commit=False)
    assert returned["recipes"] == before["recipes"]
    assert wipe.table_counts(db)["recipes"] == before["recipes"]
