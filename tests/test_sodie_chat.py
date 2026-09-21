from unittest.mock import MagicMock, patch
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


@patch("app.routers.plan_agent.ensure_user_text_allowed")
@patch("app.routers.plan_agent.ChatOpenAI")
def test_adaptive_chat_includes_context_in_prompt(
    mock_chat_openai,
    mock_moderation,
    client,
    test_user,
    test_plan,
    test_recipe_progress,
):
    mock_moderation.return_value = None
    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = "Try the pasta first."
    mock_llm.invoke.return_value = mock_response
    mock_chat_openai.return_value = mock_llm

    with patch(
        "app.routers.plan_agent.classify_message_intent",
        return_value="general_knowledge",
    ):
        response = client.post(
            f"/plan/adaptive_chat/{test_user.id}",
            json={
                "user_message": "What should I cook first?",
                "week_number": 1,
            },
        )

    assert response.status_code == 200
    assert response.json()["response"] == "Try the pasta first."

    prompt = mock_llm.invoke.call_args[0][0]
    assert "Test Recipe 0" in prompt
    assert "Italian" in prompt
    assert "ACTIVE_PLAN: week 1" in prompt


@patch("app.routers.plan_agent.ensure_user_text_allowed")
@patch("app.routers.plan_agent.ChatOpenAI")
def test_adaptive_chat_analytics_mode(
    mock_chat_openai,
    mock_moderation,
    client,
    test_user,
    test_plan,
    test_recipe_progress,
):
    mock_moderation.return_value = None
    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = "You have 0 of 1 recipes completed."
    mock_llm.invoke.return_value = mock_response
    mock_chat_openai.return_value = mock_llm

    with patch(
        "app.routers.plan_agent.classify_message_intent",
        return_value="analytics",
    ):
        response = client.post(
            f"/plan/adaptive_chat/{test_user.id}",
            json={
                "user_message": "How am I doing this week?",
                "week_number": 1,
            },
        )

    assert response.status_code == 200
    assert response.json()["intent"] == "analytics"
    prompt = mock_llm.invoke.call_args[0][0]
    assert "Mode: analytics" in prompt
    assert "0/1" in prompt
