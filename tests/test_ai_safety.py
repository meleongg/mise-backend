"""Tests for AI safety: input limits, moderation, rate-limit keys."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.core.rate_limit import get_user_id_rate_limit_key
from app.services.content_moderation import (
    MODERATION_REJECT_MESSAGE,
    ensure_user_text_allowed,
    is_llm_content_policy_error,
)


def test_get_user_id_rate_limit_key():
    request = MagicMock()
    request.path_params = {"user_id": "abc-123"}
    request.headers = {}
    request.client.host = "127.0.0.1"
    assert get_user_id_rate_limit_key(request) == "user:abc-123"


@patch("app.routers.sodie.ensure_user_text_allowed")
@patch("app.routers.sodie.invoke_chat_model")
def test_chat_rejects_overlong_message(
    mock_invoke, mock_moderation, client, test_user
):
    thread = client.post("/api/sodie/threads", json={"scope": "global"})
    assert thread.status_code == 200
    thread_id = thread.json()["id"]

    response = client.post(
        f"/api/sodie/threads/{thread_id}/chat",
        json={"content": "x" * 2001},
    )
    assert response.status_code == 422
    mock_moderation.assert_not_called()
    mock_invoke.assert_not_called()


@patch("app.routers.sodie.ChatOpenAI")
@patch("app.routers.sodie.ensure_user_text_allowed")
def test_chat_moderation_flagged_returns_400(
    mock_moderation,
    mock_chat_openai,
    client,
    test_user,
):
    mock_moderation.side_effect = HTTPException(
        status_code=400, detail=MODERATION_REJECT_MESSAGE
    )

    thread = client.post("/api/sodie/threads", json={"scope": "global"})
    assert thread.status_code == 200
    thread_id = thread.json()["id"]

    response = client.post(
        f"/api/sodie/threads/{thread_id}/chat",
        json={"content": "bad content"},
    )
    assert response.status_code == 400
    assert MODERATION_REJECT_MESSAGE in response.json()["detail"]
    mock_chat_openai.assert_not_called()


@patch("app.services.content_moderation.OpenAI")
def test_ensure_user_text_allowed_flagged(mock_openai_cls):
    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client
    mock_result = MagicMock()
    mock_result.results = [MagicMock(flagged=True)]
    mock_client.moderations.create.return_value = mock_result

    with pytest.raises(HTTPException) as exc:
        ensure_user_text_allowed("test message")
    assert exc.value.status_code == 400


@patch("app.services.content_moderation.OpenAI")
def test_ensure_user_text_allowed_passes(mock_openai_cls):
    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client
    mock_result = MagicMock()
    mock_result.results = [MagicMock(flagged=False)]
    mock_client.moderations.create.return_value = mock_result

    ensure_user_text_allowed("how do I dice an onion?")


def test_is_llm_content_policy_error_detects_marker():
    assert is_llm_content_policy_error(Exception("content_policy violation"))


@patch("app.routers.sodie.ensure_user_text_allowed")
@patch("app.routers.sodie.invoke_chat_model")
def test_chat_proceeds_when_moderation_passes(
    mock_invoke,
    mock_moderation,
    client,
    test_user,
    test_plan,
    test_recipe_progress,
):
    mock_moderation.return_value = None
    mock_invoke.return_value = "Dice in even strips."

    thread = client.post(
        "/api/sodie/threads",
        json={"scope": "plan", "context_id": str(test_plan.week_number)},
    )
    assert thread.status_code == 200
    thread_id = thread.json()["id"]

    response = client.post(
        f"/api/sodie/threads/{thread_id}/chat",
        json={"content": "How do I dice an onion?"},
    )
    assert response.status_code == 200
    assert response.json()["ai_message"]["content"] == "Dice in even strips."
    mock_moderation.assert_called_once()
    mock_invoke.assert_called_once()
