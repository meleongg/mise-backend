from datetime import datetime, timezone
from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from app.database import get_db
from app.models import SodieMessage, SodieThread, User
from app.schemas import SodieMessageCreate, SodieMessageResponse, SodieThreadCreate, SodieThreadResponse
from app.schemas import SodieChatResponse
from app.utils.auth import get_current_user
from app.constants import GENERATIVE_MODEL
from app.services.sodie_chat_context import build_sodie_chat_context
from app.services.sodie_llm import build_coach_prompt, invoke_chat_model
from langchain_openai import ChatOpenAI

router = APIRouter()

def _coach_response(user: User, thread: SodieThread, content: str, db: Session) -> str:
    context = build_sodie_chat_context(db, user)
    page_context = f"\nACTIVE PAGE: {thread.scope}" + (f" ({thread.context_id})" if thread.context_id else "")
    return invoke_chat_model(ChatOpenAI(model=GENERATIVE_MODEL, temperature=0.5), build_coach_prompt(content, context + page_context))

def _thread(db: Session, thread_id: UUID, user_id: UUID) -> SodieThread:
    thread = db.query(SodieThread).options(joinedload(SodieThread.messages)).filter(SodieThread.id == thread_id, SodieThread.user_id == user_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Sodie thread not found")
    return thread

@router.get("/threads", response_model=List[SodieThreadResponse])
def list_threads(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return db.query(SodieThread).filter(SodieThread.user_id == current_user.id, SodieThread.is_temporary.is_(False)).order_by(SodieThread.updated_at.desc()).all()

@router.post("/threads", response_model=SodieThreadResponse)
def create_thread(payload: SodieThreadCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    thread = SodieThread(user_id=current_user.id, scope=payload.scope, context_id=str(payload.context_id) if payload.context_id else None, is_temporary=payload.is_temporary)
    db.add(thread); db.commit(); db.refresh(thread)
    return thread

@router.get("/threads/{thread_id}", response_model=SodieThreadResponse)
def get_thread(thread_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _thread(db, thread_id, current_user.id)

@router.post("/threads/{thread_id}/messages", response_model=SodieMessageResponse)
def add_message(thread_id: UUID, payload: SodieMessageCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    thread = _thread(db, thread_id, current_user.id)
    message = SodieMessage(thread_id=thread.id, sender="user", content=payload.content.strip())
    thread.updated_at = datetime.now(timezone.utc)
    db.add(message); db.commit(); db.refresh(message)
    return message

@router.post("/threads/{thread_id}/chat", response_model=SodieChatResponse)
def chat(thread_id: UUID, payload: SodieMessageCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    thread = _thread(db, thread_id, current_user.id)
    user_message = SodieMessage(thread_id=thread.id, sender="user", content=payload.content.strip())
    db.add(user_message); db.flush()
    reply = _coach_response(current_user, thread, user_message.content, db)
    ai_message = SodieMessage(thread_id=thread.id, sender="ai", content=reply)
    thread.updated_at = datetime.now(timezone.utc)
    db.add(ai_message); db.commit(); db.refresh(user_message); db.refresh(ai_message)
    return SodieChatResponse(user_message=user_message, ai_message=ai_message)

@router.delete("/threads/{thread_id}", status_code=204)
def delete_thread(thread_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    db.delete(_thread(db, thread_id, current_user.id)); db.commit()
