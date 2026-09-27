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


def test_analytics_scope_snapshot_uses_progress(
    db, test_user, test_plan, test_recipe_progress
):
    text = build_sodie_prompt_context(db, test_user, scope="analytics")
    assert "ACTIVE PAGE: analytics" in text
    assert "USER PROFILE:" in text
    assert "Recipes completed:" in text
    assert "Feedback counts" in text
    assert "do not invent" in text.lower() or "Do not invent" in text


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
