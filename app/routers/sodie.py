from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import Recipe, SodieMessage, SodieThread, User
from app.schemas import (
    SodieChatResponse,
    SodieMessageCreate,
    SodieMessageResponse,
    SodieThreadCreate,
    SodieThreadResponse,
)
from app.schemas.sodie_proposals import (
    ClarifyProposalRequest,
    ProposeRecipeEditFromRequest,
    ProposeRecipeEditRequest,
    ProposeRecipeEditResponse,
    SodieActionProposalResponse,
)
from app.utils.auth import get_current_user
from app.constants import GENERATIVE_MODEL
from app.services.sodie_chat_context import build_sodie_chat_context
from app.services.sodie_llm import (
    build_coach_prompt,
    draft_to_recipe_edit_patch,
    generate_recipe_edit_patch,
    invoke_chat_model,
)
from app.services import sodie_proposals as proposals
from langchain_openai import ChatOpenAI

router = APIRouter()


def _coach_response(user: User, thread: SodieThread, content: str, db: Session) -> str:
    context = build_sodie_chat_context(db, user)
    page_context = f"\nACTIVE PAGE: {thread.scope}" + (
        f" ({thread.context_id})" if thread.context_id else ""
    )
    return invoke_chat_model(
        ChatOpenAI(model=GENERATIVE_MODEL, temperature=0.5),
        build_coach_prompt(content, context + page_context),
    )


def _thread(db: Session, thread_id: UUID, user_id: UUID) -> SodieThread:
    thread = (
        db.query(SodieThread)
        .options(joinedload(SodieThread.messages))
        .filter(SodieThread.id == thread_id, SodieThread.user_id == user_id)
        .first()
    )
    if not thread:
        raise HTTPException(status_code=404, detail="Sodie thread not found")
    return thread


def _proposal_response(proposal) -> SodieActionProposalResponse:
    return SodieActionProposalResponse(**proposals.serialize_proposal(proposal))


@router.get("/threads", response_model=List[SodieThreadResponse])
def list_threads(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return (
        db.query(SodieThread)
        .filter(SodieThread.user_id == current_user.id, SodieThread.is_temporary.is_(False))
        .order_by(SodieThread.updated_at.desc())
        .all()
    )


@router.post("/threads", response_model=SodieThreadResponse)
def create_thread(
    payload: SodieThreadCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    thread = SodieThread(
        user_id=current_user.id,
        scope=payload.scope,
        context_id=str(payload.context_id) if payload.context_id else None,
        is_temporary=payload.is_temporary,
    )
    db.add(thread)
    db.commit()
    db.refresh(thread)
    return thread


@router.get("/threads/{thread_id}", response_model=SodieThreadResponse)
def get_thread(
    thread_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _thread(db, thread_id, current_user.id)


@router.post("/threads/{thread_id}/messages", response_model=SodieMessageResponse)
def add_message(
    thread_id: UUID,
    payload: SodieMessageCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    thread = _thread(db, thread_id, current_user.id)
    message = SodieMessage(
        thread_id=thread.id, sender="user", content=payload.content.strip()
    )
    thread.updated_at = datetime.now(timezone.utc)
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


@router.post("/threads/{thread_id}/chat", response_model=SodieChatResponse)
def chat(
    thread_id: UUID,
    payload: SodieMessageCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    thread = _thread(db, thread_id, current_user.id)
    user_message = SodieMessage(
        thread_id=thread.id, sender="user", content=payload.content.strip()
    )
    db.add(user_message)
    db.flush()
    reply = _coach_response(current_user, thread, user_message.content, db)
    ai_message = SodieMessage(thread_id=thread.id, sender="ai", content=reply)
    thread.updated_at = datetime.now(timezone.utc)
    db.add(ai_message)
    db.commit()
    db.refresh(user_message)
    db.refresh(ai_message)
    return SodieChatResponse(user_message=user_message, ai_message=ai_message, proposal=None)


@router.delete("/threads/{thread_id}", status_code=204)
def delete_thread(
    thread_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    db.delete(_thread(db, thread_id, current_user.id))
    db.commit()


@router.post("/proposals", response_model=ProposeRecipeEditResponse)
def create_proposal(
    payload: ProposeRecipeEditRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if payload.thread_id:
        _thread(db, payload.thread_id, current_user.id)
    proposal = proposals.propose_recipe_edit(
        db,
        current_user,
        source_recipe_id=payload.source_recipe_id,
        patch=payload.patch,
        idempotency_key=payload.idempotency_key,
        rationale=payload.rationale,
        thread_id=payload.thread_id,
    )
    assistant = (
        "I prepared a personal recipe edit for your review. "
        "Approve to save a personal copy — the shared catalog recipe stays unchanged."
    )
    if payload.thread_id:
        thread = _thread(db, payload.thread_id, current_user.id)
        message = SodieMessage(thread_id=thread.id, sender="ai", content=assistant)
        thread.updated_at = datetime.now(timezone.utc)
        db.add(message)
        db.commit()
    return ProposeRecipeEditResponse(
        proposal=_proposal_response(proposal), assistant_message=assistant
    )


@router.post("/proposals/from-request", response_model=ProposeRecipeEditResponse)
def create_proposal_from_request(
    payload: ProposeRecipeEditFromRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Draft an allowlisted patch from natural language via structured LLM, then propose."""
    if payload.thread_id:
        _thread(db, payload.thread_id, current_user.id)

    recipe = db.query(Recipe).filter(Recipe.id == payload.source_recipe_id).first()
    if not recipe:
        raise HTTPException(status_code=404, detail="Source recipe not found")

    snapshot = proposals.recipe_content_snapshot(recipe)
    draft = generate_recipe_edit_patch(snapshot, payload.request)
    if draft.ambiguous:
        raise HTTPException(
            status_code=422,
            detail=draft.change_summary
            or "Could not map that request to a concrete recipe change.",
        )

    patch = draft_to_recipe_edit_patch(draft)
    if not patch.model_dump(exclude_none=True):
        raise HTTPException(
            status_code=422,
            detail="Could not map that request to a concrete recipe change.",
        )

    proposal = proposals.propose_recipe_edit(
        db,
        current_user,
        source_recipe_id=payload.source_recipe_id,
        patch=patch,
        idempotency_key=payload.idempotency_key,
        rationale=draft.change_summary or payload.request,
        thread_id=payload.thread_id,
    )
    assistant = "Here’s a proposal from what you asked for."
    if payload.thread_id:
        thread = _thread(db, payload.thread_id, current_user.id)
        message = SodieMessage(thread_id=thread.id, sender="ai", content=assistant)
        thread.updated_at = datetime.now(timezone.utc)
        db.add(message)
        db.commit()
    return ProposeRecipeEditResponse(
        proposal=_proposal_response(proposal), assistant_message=assistant
    )


@router.get("/proposals/{proposal_id}", response_model=SodieActionProposalResponse)
def get_proposal(
    proposal_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _proposal_response(proposals.get_proposal(db, current_user, proposal_id))


@router.post("/proposals/{proposal_id}/approve", response_model=SodieActionProposalResponse)
def approve_proposal(
    proposal_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _proposal_response(proposals.apply_recipe_edit(db, current_user, proposal_id))


@router.post("/proposals/{proposal_id}/reject", response_model=SodieActionProposalResponse)
def reject_proposal(
    proposal_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _proposal_response(proposals.reject_proposal(db, current_user, proposal_id))


@router.post("/proposals/{proposal_id}/clarify", response_model=SodieMessageResponse)
def clarify_proposal(
    proposal_id: UUID,
    payload: ClarifyProposalRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    proposal = proposals.get_proposal(db, current_user, proposal_id)
    if proposal.status != "pending":
        raise HTTPException(status_code=409, detail="Only pending proposals can be clarified")
    if not proposal.thread_id:
        raise HTTPException(status_code=422, detail="Proposal has no thread for clarification")
    thread = _thread(db, proposal.thread_id, current_user.id)
    message = SodieMessage(
        thread_id=thread.id,
        sender="user",
        content=f"Clarify proposal: {payload.content.strip()}",
    )
    thread.updated_at = datetime.now(timezone.utc)
    db.add(message)
    db.commit()
    db.refresh(message)
    return message
