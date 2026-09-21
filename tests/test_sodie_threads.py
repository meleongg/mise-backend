from unittest.mock import patch
import uuid


def test_thread_message_and_temporary_history(client, test_user):
    regular = client.post("/api/sodie/threads", json={"scope": "recipe"})
    assert regular.status_code == 200
    thread_id = regular.json()["id"]
    message = client.post(
        f"/api/sodie/threads/{thread_id}/messages",
        json={"content": "Can I prep this now?"},
    )
    assert message.status_code == 200
    assert message.json()["sender"] == "user"
    assert len(client.get(f"/api/sodie/threads/{thread_id}").json()["messages"]) == 1
    assert client.post("/api/sodie/threads", json={"is_temporary": True}).status_code == 200
    history = client.get("/api/sodie/threads")
    assert history.status_code == 200
    assert len(history.json()) == 1


def test_list_threads_filters_by_scope_and_context(
    client, test_user, test_plan, test_recipes
):
    plan = client.post(
        "/api/sodie/threads",
        json={"scope": "plan", "context_id": str(test_plan.week_number)},
    )
    recipe = client.post(
        "/api/sodie/threads",
        json={"scope": "recipe", "context_id": str(test_recipes[0].id)},
    )
    assert plan.status_code == 200
    assert recipe.status_code == 200

    by_plan = client.get(
        "/api/sodie/threads",
        params={"scope": "plan", "context_id": str(test_plan.week_number)},
    )
    assert by_plan.status_code == 200
    assert len(by_plan.json()) == 1
    assert by_plan.json()[0]["id"] == plan.json()["id"]

    by_recipe = client.get(
        "/api/sodie/threads",
        params={"scope": "recipe", "context_id": str(test_recipes[0].id)},
    )
    assert by_recipe.status_code == 200
    assert len(by_recipe.json()) == 1
    assert by_recipe.json()[0]["id"] == recipe.json()["id"]


def test_create_thread_rejects_unknown_recipe_context(client, test_user):
    res = client.post(
        "/api/sodie/threads",
        json={"scope": "recipe", "context_id": str(uuid.uuid4())},
    )
    assert res.status_code == 404


@patch("app.routers.sodie._coach_response", return_value="Yes — prep the vegetables first.")
def test_chat_persists_user_and_ai_messages(mock_coach, client, test_user):
    thread_id = client.post("/api/sodie/threads", json={}).json()["id"]
    response = client.post(
        f"/api/sodie/threads/{thread_id}/chat", json={"content": "Can I prep ahead?"}
    )
    assert response.status_code == 200
    assert response.json()["ai_message"]["content"] == "Yes — prep the vegetables first."
    msgs = client.get(f"/api/sodie/threads/{thread_id}").json()["messages"]
    assert len(msgs) == 2
    assert [m["sender"] for m in msgs] == ["user", "ai"]
