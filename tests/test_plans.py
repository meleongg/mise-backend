import json


def test_get_all_weekly_plans(client, test_user, test_plan):
    """Test fetching all weekly plans"""
    response = client.get(f"/api/weekly-plan/{test_user.id}/all")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert data[0]["week_number"] == 1


def test_get_weekly_plan(client, test_user, test_plan):
    """Test fetching a specific weekly plan"""
    response = client.get(f"/api/weekly-plan?user_id={test_user.id}&week_number=1")
    assert response.status_code == 200
    data = response.json()
    assert data["week_number"] == 1
    assert data["swap_count"] == 0


def test_weekly_plan_recipes_follow_schedule_order(
    client, test_user, test_plan, test_recipes, db
):
    """The API returns recipes in the explicit schedule order, not DB order."""
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
    assert [recipe["id"] for recipe in response.json()["recipes"]] == [
        str(test_recipes[1].id),
        str(test_recipes[0].id),
    ]


def test_regenerating_existing_plan_requires_explicit_confirmation(
    client, test_user, test_plan
):
    response = client.post(
        f"/plan/generate/{test_user.id}",
        json={"initial_intent": "Create a fresh Italian plan"},
    )

    assert response.status_code == 409
    assert "confirmation" in response.json()["detail"].lower()


def test_get_weekly_progress_most_recent(client, test_user, test_plan):
    """Test getting progress for most recent week"""
    response = client.get(f"/api/progress/{test_user.id}/week")
    # Will be 404 if no progress entries (expected for new plan)
    assert response.status_code in [200, 404]


def test_get_weekly_progress_specific_week(client, test_user, test_plan):
    """Test getting progress for specific week"""
    response = client.get(f"/api/progress/{test_user.id}/week/1")
    assert response.status_code in [200, 404]
