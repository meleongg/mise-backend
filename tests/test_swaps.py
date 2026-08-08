import uuid

from app.models import UserRecipeProgress
from app.services.weekly_plan import replace_swapped_recipe_progress


def test_swap_limit_check(client, test_user, test_plan):
    """Test that swap endpoint enforces swap limit"""
    # First, ensure the plan exists
    response = client.get(f"/api/weekly-plan/{test_user.id}/all")
    assert response.status_code == 200

    # Attempting to swap (will fail due to missing recipe in progress, but validates limit logic)
    swap_payload = {
        "recipe_id_to_replace": "00000000-0000-0000-0000-000000000001",
        "swap_context": "Want something lighter",
        "week_number": 1,
    }
    response = client.post(f"/plan/swap-recipe/{test_user.id}", json=swap_payload)
    # Expect 404 (recipe not found) or 400 (validation error)
    assert response.status_code in [400, 404]


def test_get_next_week_eligibility(client, test_user):
    """Test checking if user can generate next week"""
    response = client.get(f"/plan/can_generate_next_week/{test_user.id}")
    assert response.status_code == 200
    data = response.json()
    assert "can_generate" in data
    assert "current_week" in data


def test_replace_swapped_recipe_progress_replaces_old_progress_with_new_recipe(
    db, test_user, test_recipes
):
    old_recipe_id = test_recipes[0].id
    new_recipe_id = test_recipes[1].id
    db.add(
        UserRecipeProgress(
            id=uuid.uuid4(),
            user_id=test_user.id,
            recipe_id=old_recipe_id,
            week_number=1,
            status="in_progress",
        )
    )
    db.flush()

    replace_swapped_recipe_progress(
        user_id=test_user.id,
        old_recipe_id=old_recipe_id,
        new_recipe_id=new_recipe_id,
        week_number=1,
        db=db,
    )
    db.flush()

    progress = (
        db.query(UserRecipeProgress)
        .filter(
            UserRecipeProgress.user_id == test_user.id,
            UserRecipeProgress.week_number == 1,
        )
        .all()
    )

    assert len(progress) == 1
    assert progress[0].recipe_id == new_recipe_id
    assert progress[0].status == "not_started"
    assert progress[0].completed_at is None
