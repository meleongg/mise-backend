def test_submit_feedback(client, test_user, test_recipe_progress, test_recipes):
    """Test submitting recipe feedback"""
    response = client.post(
        f"/api/feedback/{test_user.id}",
        json={
            "recipe_id": str(test_recipes[0].id),
            "week_number": 1,
            "feedback": "just_right",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert data["feedback"] == "just_right"


def test_get_progress_summary(client, test_user):
    """Test getting progress summary"""
    response = client.get(f"/api/progress/{test_user.id}")
    assert response.status_code in [200, 404]


def test_patch_status_in_progress(
    client, test_user, test_recipe_progress, test_recipes
):
    """PATCH in_progress sets status without completed_at"""
    recipe_id = test_recipes[0].id
    response = client.patch(
        f"/api/progress/{test_user.id}/recipe/{recipe_id}/week/1",
        json={"status": "in_progress"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "in_progress"
    assert data["completed_at"] is None


def test_patch_status_not_started_clears_notes(
    client, db, test_user, test_recipe_progress, test_recipes
):
    """PATCH not_started clears feedback and notes"""
    recipe_id = test_recipes[0].id
    test_recipe_progress.status = "completed"
    test_recipe_progress.feedback = "just_right"
    test_recipe_progress.notes = "Great meal"
    db.flush()
    db.refresh(test_recipe_progress)

    response = client.patch(
        f"/api/progress/{test_user.id}/recipe/{recipe_id}/week/1",
        json={"status": "not_started"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "not_started"
    assert data["feedback"] is None
    assert data["notes"] is None


def test_submit_feedback_with_notes(
    client, test_user, test_recipe_progress, test_recipes
):
    """POST feedback persists optional notes"""
    response = client.post(
        f"/api/feedback/{test_user.id}",
        json={
            "recipe_id": str(test_recipes[0].id),
            "week_number": 1,
            "feedback": "too_easy",
            "notes": "Kids loved it",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert data["feedback"] == "too_easy"
    assert data["notes"] == "Kids loved it"
