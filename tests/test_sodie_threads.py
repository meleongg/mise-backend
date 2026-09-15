def test_thread_message_and_temporary_history(client, test_user):
    regular = client.post("/api/sodie/threads", json={"scope": "recipe"})
    assert regular.status_code == 200
    thread_id = regular.json()["id"]
    message = client.post(f"/api/sodie/threads/{thread_id}/messages", json={"content": "Can I prep this now?"})
    assert message.status_code == 200
    assert message.json()["sender"] == "user"
    assert len(client.get(f"/api/sodie/threads/{thread_id}").json()["messages"]) == 1
    assert client.post("/api/sodie/threads", json={"is_temporary": True}).status_code == 200
    history = client.get("/api/sodie/threads")
    assert history.status_code == 200
    assert len(history.json()) == 1
