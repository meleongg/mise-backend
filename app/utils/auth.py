from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from datetime import datetime, timedelta, timezone
from uuid import UUID
from app.models import User
from app.database import get_db
from app.constants import (
    SECRET_KEY,
    ALGORITHM,
    ACCESS_TOKEN_EXPIRE_MINUTES,
    REFRESH_TOKEN_EXPIRE_DAYS,
)
from sqlalchemy.orm import Session

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def require_same_user(current_user: User, user_id: UUID) -> None:
    """Ensure the authenticated user can only access their own resources."""
    if current_user.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only access your own data",
        )


def _create_token(user_id: str, token_type: str, expires_delta: timedelta) -> str:
    expire = datetime.now(timezone.utc) + expires_delta
    payload = {"user_id": user_id, "type": token_type, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def create_access_token(user_id: str) -> str:
    return _create_token(
        user_id=user_id,
        token_type="access",
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )


def create_refresh_token(user_id: str) -> str:
    return _create_token(
        user_id=user_id,
        token_type="refresh",
        expires_delta=timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
    )


def decode_token(token: str, expected_type: str) -> dict:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token"
        )
    token_type = payload.get("type")
    if token_type != expected_type:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type"
        )
    return payload


async def get_current_user(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User:
    payload = decode_token(token, expected_type="access")
    user_id = payload.get("user_id")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload"
        )
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found"
        )
    return user
