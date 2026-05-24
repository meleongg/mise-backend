from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from uuid import UUID
from app.database import get_db
from app.utils.auth import get_current_user, require_same_user
from app.utils.password import hash_password, verify_password
from app.models import User
from app.schemas import (
    UserCreate,
    UserUpdate,
    UserResponse,
    UpdateAccountDetails,
    ChangePasswordRequest,
    MessageResponse,
)

router = APIRouter()


@router.put(
    "/user/profile", response_model=UserResponse, status_code=status.HTTP_200_OK
)
async def update_user_profile(
    user_data: UserCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Update the authenticated user's profile after onboarding"""
    user = db.query(User).filter(User.id == current_user.id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    for field, value in user_data.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    db.commit()
    db.refresh(user)
    return user


@router.get("/user/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: UUID, db: Session = Depends(get_db), current_user=Depends(get_current_user)
):
    """Get user profile by ID"""
    require_same_user(current_user, user_id)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    return user


@router.put("/user/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: UUID,
    user_data: UserUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Update user profile"""
    require_same_user(current_user, user_id)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    # Only update allowed fields (not name)
    for field, value in user_data.model_dump(exclude_unset=True).items():
        if field != "name":
            setattr(user, field, value)
    db.commit()
    db.refresh(user)
    return user


@router.get("/users", response_model=List[UserResponse])
async def list_users(
    db: Session = Depends(get_db), current_user=Depends(get_current_user)
):
    """Return only the authenticated user's profile."""
    return [current_user]


@router.put("/users/{user_id}/account", response_model=UserResponse)
async def update_account_details(
    user_id: UUID,
    account_data: UpdateAccountDetails,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Update account details (email, first name, last name)"""
    require_same_user(current_user, user_id)

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    # Check if email is being changed and if it's already taken
    if account_data.email and account_data.email != user.email:
        existing_user = db.query(User).filter(User.email == account_data.email).first()
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already in use",
            )

    # Update fields
    for field, value in account_data.model_dump(exclude_unset=True).items():
        setattr(user, field, value)

    db.commit()
    db.refresh(user)
    return user


@router.put("/users/{user_id}/password", response_model=MessageResponse)
async def change_password(
    user_id: UUID,
    password_data: ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Change user password"""
    require_same_user(current_user, user_id)

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    # Verify current password
    if not verify_password(password_data.current_password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    # Hash and update new password
    user.hashed_password = hash_password(password_data.new_password)
    db.commit()

    return {"message": "Password changed successfully"}


@router.delete("/users/{user_id}", response_model=MessageResponse)
async def delete_account(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Delete user account (soft delete or hard delete based on requirements)"""
    require_same_user(current_user, user_id)

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    # Hard delete (you can implement soft delete if needed)
    db.delete(user)
    db.commit()

    return {"message": "Account deleted successfully"}
