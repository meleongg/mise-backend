from unittest.mock import patch
import uuid

from fastapi import HTTPException

from app.models import User
from app.services.sodie_chat_context import (
    authorize_page_context,
    build_sodie_chat_context,
    build_sodie_prompt_context,
)
import app.utils.password as password_utils


def test_build_sodie_chat_context_no_plan(db, test_user):
    context = build_sodie_chat_context(db, test_user, week_number=None)
    assert "ACTIVE_PLAN: none" in context
    assert test_user.first_name in context
    assert test_user.cuisine in context


def test_build_sodie_chat_context_with_plan(
    db, test_user, test_plan, test_recipes, test_recipe_progress
):
    recipe = test_recipes[0]
    recipe.dietary_tags = '["vegetarian"]'
    recipe.allergens = '["dairy"]'
    db.flush()

    context = build_sodie_chat_context(db, test_user, week_number=1)
    assert "ACTIVE_PLAN: week 1" in context
    assert recipe.name in context
    assert "[not_started]" in context
    assert "Swaps remaining" in context
    assert "vegetarian" in context
    assert "dairy" in context


def test_recipe_page_snapshot_includes_ingredients(db, test_user, test_recipes):
    recipe = test_recipes[0]
    snapshot, week = authorize_page_context(db, test_user, "recipe", str(recipe.id))
    assert week is None
    assert "ACTIVE PAGE: recipe" in snapshot
    assert recipe.name in snapshot
    assert "Ingredients:" in snapshot
    assert "Instructions:" in snapshot


def test_plan_page_context_denied_for_other_user(db, test_user, test_plan):
    other = User(
        id=uuid.uuid4(),
        email="other-page-context@example.com",
        first_name="Other",
        last_name="User",
        cuisine="Italian",
        frequency=3,
        skill_level="intermediate",
        user_goal="Learn New Techniques",
        hashed_password=password_utils.hash_password("OtherUser123!"),
    )
    db.add(other)
    db.flush()
    try:
        authorize_page_context(db, other, "plan", str(test_plan.id))
        assert False, "expected 404"
    except HTTPException as exc:
        assert exc.status_code == 404


def test_prompt_context_combines_profile_and_page(db, test_user, test_recipes):
    recipe = test_recipes[0]
    text = build_sodie_prompt_context(
        db, test_user, scope="kitchen", context_id=str(recipe.id)
    )
    assert "USER PROFILE:" in text
    assert "ACTIVE PAGE: kitchen" in text
    assert recipe.name in text
    assert "Kitchen Mode" in text


def test_kitchen_live_state_appended_only_for_kitchen_scope(
    db, test_user, test_recipes
):
    from app.services.sodie_chat_context import format_kitchen_live_state

    recipe = test_recipes[0]
    state = {
        "current_step_index": 1,
        "total_steps": 4,
        "current_step_text": "Simmer the sauce for 10 minutes.",
        "checked_ingredients": 3,
        "total_ingredients": 8,
    }
    kitchen_text = build_sodie_prompt_context(
        db,
        test_user,
        scope="kitchen",
        context_id=str(recipe.id),
        kitchen_state=state,
    )
    assert "KITCHEN LIVE STATE:" in kitchen_text
    assert "Current step: 2 of 4" in kitchen_text
    assert "Simmer the sauce for 10 minutes." in kitchen_text
    assert "3 of 8 ingredients" in kitchen_text

    recipe_text = build_sodie_prompt_context(
        db,
        test_user,
        scope="recipe",
        context_id=str(recipe.id),
        kitchen_state=state,
    )
    assert "KITCHEN LIVE STATE:" not in recipe_text

    live = format_kitchen_live_state(state)
    assert "Prefer concise help" in live


def test_kitchen_live_state_includes_active_timers(db, test_user, test_recipes):
    from app.services.sodie_chat_context import format_kitchen_live_state

    recipe = test_recipes[0]
    state = {
        "current_step_index": 0,
        "total_steps": 2,
        "current_step_text": "Simmer for 10 minutes.",
        "checked_ingredients": 0,
        "total_ingredients": 2,
        "active_timers": [
            {"label": "Simmer", "remaining_seconds": 605},
            {"label": "Rest dough", "remaining_seconds": 90},
        ],
    }
    text = build_sodie_prompt_context(
        db,
        test_user,
        scope="kitchen",
        context_id=str(recipe.id),
        kitchen_state=state,
    )
    assert "Active timers:" in text
    assert "Simmer: 10m 05s remaining" in text
    assert "Rest dough: 1m 30s remaining" in text
    assert "Do not invent additional timers" in text

    live = format_kitchen_live_state(state)
    assert "Prefer concise help" not in live


def test_settings_scope_omits_profile_and_plan(db, test_user, test_plan):
    text = build_sodie_prompt_context(db, test_user, scope="settings")
    assert "ACTIVE PAGE: settings" in text
    assert "USER PROFILE:" not in text
    assert "ACTIVE_PLAN:" not in text
    assert test_user.first_name not in text


def test_personal_recipe_snapshot_is_owner_only(db, test_user, test_recipes):
    from app.models import PersonalRecipe

    personal = PersonalRecipe(
        user_id=test_user.id,
        source_recipe_id=test_recipes[0].id,
        name="My edited pasta",
        ingredients='[{"name":"oats","measure":"1 cup"}]',
        instructions='[{"text":"Stir."}]',
        portion_size="2",
        notes="extra oats",
        current_revision=2,
        is_active=True,
    )
    db.add(personal)
    db.flush()

    snapshot, week = authorize_page_context(
        db, test_user, "personal_recipe", str(personal.id)
    )
    assert week is None
    assert "ACTIVE PAGE: personal_recipe" in snapshot
    assert "My edited pasta" in snapshot
    assert "oats" in snapshot
    assert "extra oats" in snapshot

    other = User(
        id=uuid.uuid4(),
        email="other-personal@example.com",
        first_name="Other",
        last_name="User",
        cuisine="Italian",
        frequency=3,
        skill_level="intermediate",
        user_goal="Learn New Techniques",
        hashed_password=password_utils.hash_password("OtherUser123!"),
    )
    db.add(other)
    db.flush()
    try:
        authorize_page_context(db, other, "personal_recipe", str(personal.id))
        assert False, "expected 404"
    except HTTPException as exc:
        assert exc.status_code == 404


def test_coach_prompt_analytics_mode_rules():
    from app.services.sodie_llm import build_coach_prompt

    prompt = build_coach_prompt(
        "How am I doing this week?",
        "ACTIVE_PLAN: week 1\n- Week progress: 0/1 recipes completed",
        mode="analytics",
    )
    assert "Mode: analytics" in prompt
    assert "0/1" in prompt


def test_coach_rules_redirect_edits_to_edit_with_sodie():
    from app.services.sodie_llm import SODIE_BASE_RULES, build_coach_prompt

    rules = SODIE_BASE_RULES.lower()
    assert "edit with sodie" in rules
    assert "cannot create or submit" in rules
    assert "verbal proposal" in rules
    prompt = build_coach_prompt(
        "Can you add more salt?",
        "ACTIVE PAGE: recipe\n- Title: Cookies\n",
        mode="general_knowledge",
    )
    assert "Edit with Sodie" in prompt


def test_analytics_scope_snapshot_uses_progress(
    db, test_user, test_plan, test_recipe_progress
):
    text = build_sodie_prompt_context(db, test_user, scope="analytics")
    assert "ACTIVE PAGE: analytics" in text
    assert "USER PROFILE:" in text
    assert "Recipes completed:" in text
    assert "Feedback counts" in text
    assert "do not invent" in text.lower() or "Do not invent" in text


def test_shopping_scope_returns_plan_week_not_list_id(
    client, db, test_user, test_recipes, test_plan
):
    """Regression: shopping authorize must not pass list UUID as week_number."""
    import json
    import uuid
    from datetime import datetime, timezone

    from app.models import WeeklyPlanEntry
    from app.services.sodie_chat_context import authorize_page_context
    from app.services.weekly_plan import create_recipe_schedule

    test_plan.recipe_schedule = create_recipe_schedule([str(test_recipes[0].id)])
    db.add(
        WeeklyPlanEntry(
            id=uuid.uuid4(),
            weekly_plan_id=test_plan.id,
            position=0,
            catalog_recipe_id=test_recipes[0].id,
            recipe_snapshot=json.dumps(
                {
                    "id": str(test_recipes[0].id),
                    "name": test_recipes[0].name,
                    "ingredients": [{"name": "Onion", "measure": "1 cup"}],
                }
            ),
            lifecycle_state="planned",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
    )
    db.flush()

    generated = client.post(
        "/api/shopping-lists/generate",
        json={"week_number": test_plan.week_number},
    )
    assert generated.status_code == 200
    list_id = generated.json()["id"]

    snapshot, week = authorize_page_context(db, test_user, "shopping", None)
    assert "ACTIVE PAGE: shopping" in snapshot
    assert "Onion" in snapshot or "onion" in snapshot.lower()
    assert week == test_plan.week_number
    assert week != list_id
    assert not isinstance(week, str)

    # Must not raise ProgrammingError when building full prompt context
    text = build_sodie_prompt_context(db, test_user, scope="shopping")
    assert "ACTIVE PAGE: shopping" in text
    assert "USER PROFILE:" in text


@patch("app.routers.sodie.ensure_user_text_allowed")
@patch("app.routers.sodie.invoke_chat_model")
def test_sodie_chat_includes_context_in_prompt(
    mock_invoke,
    mock_moderation,
    client,
    test_user,
    test_plan,
    test_recipe_progress,
):
    mock_moderation.return_value = None
    mock_invoke.return_value = "Try the pasta first."

    thread = client.post(
        "/api/sodie/threads",
        json={"scope": "plan", "context_id": str(test_plan.week_number)},
    )
    assert thread.status_code == 200
    thread_id = thread.json()["id"]

    response = client.post(
        f"/api/sodie/threads/{thread_id}/chat",
        json={"content": "What should I cook first?"},
    )

    assert response.status_code == 200
    assert response.json()["ai_message"]["content"] == "Try the pasta first."

    prompt = mock_invoke.call_args[0][1]
    assert "Test Recipe 0" in prompt
    assert "Italian" in prompt
    assert "ACTIVE_PLAN: week 1" in prompt
    assert "ACTIVE PAGE: plan" in prompt
