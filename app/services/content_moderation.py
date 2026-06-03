"""
OpenAI Moderation API checks for user-authored text before LLM calls.
"""

from __future__ import annotations

import logging
import os

from fastapi import HTTPException, status
from openai import OpenAI

logger = logging.getLogger(__name__)

MODERATION_REJECT_MESSAGE = (
    "That message can't be processed. Please keep questions cooking-related."
)

LLM_POLICY_REJECT_MESSAGE = (
    "Sodie couldn't respond right now. Try rephrasing your question."
)


def moderation_enabled() -> bool:
    if os.getenv("MODERATION_ENABLED", "true").lower() in ("0", "false", "no"):
        return False
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


def ensure_user_text_allowed(text: str) -> None:
    """
    Run OpenAI moderation on user-supplied text. Raises HTTP 400 if flagged.
    """
    stripped = (text or "").strip()
    if not stripped:
        return

    if not moderation_enabled():
        return

    try:
        client = OpenAI()
        result = client.moderations.create(
            input=stripped,
            model=os.getenv("OPENAI_MODERATION_MODEL", "omni-moderation-latest"),
        )
        flagged = bool(result.results and result.results[0].flagged)
    except Exception as exc:
        logger.warning("Moderation API error: %s", exc)
        return

    if flagged:
        logger.info("Moderation flagged user text (len=%s)", len(stripped))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=MODERATION_REJECT_MESSAGE,
        )


def is_llm_content_policy_error(exc: BaseException) -> bool:
    """Detect OpenAI / LangChain content policy failures."""
    message = str(exc).lower()
    markers = (
        "content_policy",
        "content policy",
        "policy violation",
        "safety",
        "responsibleaipolicy",
    )
    return any(m in message for m in markers)
