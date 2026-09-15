from datetime import datetime, timezone
from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from app.database import get_db
from app.models import SodieMessage, SodieThread, User
from app.schemas import SodieMessageCreate, SodieMessageResponse, SodieThreadCreate, SodieThreadResponse
from app.utils.auth import get_current_user

router = APIRouter()

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

@router.delete("/threads/{thread_id}", status_code=204)
def delete_thread(thread_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    db.delete(_thread(db, thread_id, current_user.id)); db.commit()
