"""Tests for AI safety: input limits, moderation, rate-limit keys."""

import uuid
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


@patch("app.routers.plan_agent.ChatOpenAI")
@patch("app.routers.plan_agent.ensure_user_text_allowed")
def test_chat_rejects_overlong_message(
    mock_moderation, mock_chat_openai, client, test_user
):
    mock_moderation.return_value = None
    long_message = "x" * 2001
    response = client.post(
        f"/plan/adaptive_chat/{test_user.id}",
        json={"user_message": long_message, "week_number": 1},
    )
    assert response.status_code == 422
    mock_moderation.assert_not_called()


@patch(
    "app.routers.plan_agent.classify_message_intent", return_value="general_knowledge"
)
@patch("app.routers.plan_agent.ChatOpenAI")
@patch("app.routers.plan_agent.ensure_user_text_allowed")
def test_chat_moderation_flagged_returns_400(
    mock_moderation,
    mock_chat_openai,
    mock_classify,
    client,
    test_user,
    test_plan,
    test_recipe_progress,
):
    mock_moderation.side_effect = HTTPException(
        status_code=400, detail=MODERATION_REJECT_MESSAGE
    )

    response = client.post(
        f"/plan/adaptive_chat/{test_user.id}",
        json={"user_message": "bad content", "week_number": 1},
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


@patch("app.routers.plan_agent.ensure_user_text_allowed")
@patch(
    "app.routers.plan_agent.classify_message_intent", return_value="general_knowledge"
)
@patch("app.routers.plan_agent.ChatOpenAI")
def test_chat_proceeds_when_moderation_passes(
    mock_chat_openai,
    mock_classify,
    mock_moderation,
    client,
    test_user,
    test_plan,
    test_recipe_progress,
):
    mock_moderation.return_value = None
    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = "Dice in even strips."
    mock_llm.invoke.return_value = mock_response
    mock_chat_openai.return_value = mock_llm

    response = client.post(
        f"/plan/adaptive_chat/{test_user.id}",
        json={"user_message": "How do I dice an onion?", "week_number": 1},
    )
    assert response.status_code == 200
    mock_moderation.assert_called_once()
