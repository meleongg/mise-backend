from typing import List
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.utils.auth import get_current_user, require_same_user
from app.schemas import (
    FeedbackCreate,
    UserRecipeProgressResponse,
    ProgressSummary,
    UpdateRecipeStatus,
)
from app.services.weekly_plan import WeeklyPlanService
from app.models import UserRecipeProgress, User, Recipe
from uuid import UUID
from datetime import datetime, timezone

router = APIRouter()
plan_service = WeeklyPlanService()


@router.post("/feedback/{user_id}", response_model=UserRecipeProgressResponse)
async def submit_feedback(
    user_id: UUID,
    feedback_data: FeedbackCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Log recipe completion and feedback (supports both update and create)"""
    require_same_user(current_user, user_id)
    # Try to update existing progress first
    success = plan_service.process_feedback(
        user_id,
        feedback_data.recipe_id,
        feedback_data.week_number,
        feedback_data.feedback,
        db,
        notes=feedback_data.notes,
    )

    if not success:
        # If no existing progress entry, create one (safety net)
        # This shouldn't normally happen if generate_weekly_plan works correctly
        from datetime import datetime, timezone

        new_progress = UserRecipeProgress(
            user_id=user_id,
            recipe_id=feedback_data.recipe_id,
            week_number=feedback_data.week_number,
            status="completed",
            feedback=feedback_data.feedback,
            notes=(feedback_data.notes.strip() if feedback_data.notes else None),
            completed_at=datetime.now(timezone.utc),
        )
        db.add(new_progress)
        db.commit()
        db.refresh(new_progress)
        return new_progress

    # Return updated progress
    progress = (
        db.query(UserRecipeProgress)
        .filter(
            UserRecipeProgress.user_id == user_id,
            UserRecipeProgress.recipe_id == feedback_data.recipe_id,
            UserRecipeProgress.week_number == feedback_data.week_number,
        )
        .first()
    )
    return progress


@router.get("/progress/{user_id}", response_model=ProgressSummary)
async def get_progress_path(
    user_id: UUID, db: Session = Depends(get_db), current_user=Depends(get_current_user)
):
    """Get user progress summary (path version)"""
    require_same_user(current_user, user_id)
    summary = plan_service.get_progress_summary(user_id, db)
    if not summary:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    return ProgressSummary(**summary)


@router.get(
    "/progress/{user_id}/week",
    response_model=List[UserRecipeProgressResponse],
)
async def get_weekly_progress(
    user_id: UUID,
    week_number: int = Query(
        None, description="Specific week number (optional, defaults to most recent)"
    ),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Get all recipe progress for a user in a specific week.
    Defaults to most recent week when week_number is not provided.
    """
    require_same_user(current_user, user_id)
    return _fetch_weekly_progress(user_id, week_number, db)


@router.get(
    "/progress/{user_id}/week/{week_number}",
    response_model=List[UserRecipeProgressResponse],
)
async def get_weekly_progress_by_week(
    user_id: UUID,
    week_number: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Get all recipe progress for a user in a specific week."""
    require_same_user(current_user, user_id)
    return _fetch_weekly_progress(user_id, week_number, db)


def _fetch_weekly_progress(
    user_id: UUID, week_number: int, db: Session
) -> List[UserRecipeProgress]:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # If no week specified, use the most recent week with data
    if week_number is None:
        week_number = plan_service.get_current_week(user, db)

    progress_records = (
        db.query(UserRecipeProgress)
        .filter(
            UserRecipeProgress.user_id == user_id,
            UserRecipeProgress.week_number == week_number,
        )
        .all()
    )
    return progress_records


@router.get(
    "/progress/{user_id}/recipe/{recipe_id}/week/{week_number}",
    response_model=UserRecipeProgressResponse,
)
async def get_recipe_progress(
    user_id: UUID,
    recipe_id: UUID,
    week_number: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Get progress for a specific recipe in a specific week"""
    require_same_user(current_user, user_id)
    progress = (
        db.query(UserRecipeProgress)
        .filter(
            UserRecipeProgress.user_id == user_id,
            UserRecipeProgress.recipe_id == recipe_id,
            UserRecipeProgress.week_number == week_number,
        )
        .first()
    )

    if not progress:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Recipe progress not found"
        )

    return progress


@router.patch(
    "/progress/{user_id}/recipe/{recipe_id}/week/{week_number}",
    response_model=UserRecipeProgressResponse,
)
async def update_recipe_status(
    user_id: UUID,
    recipe_id: UUID,
    week_number: int,
    status_update: UpdateRecipeStatus,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Update the status of a recipe (mark as incomplete/complete).

    This allows users to toggle between completed and not_started states.
    When marking as incomplete, feedback and rating are cleared.
    """
    require_same_user(current_user, user_id)

    # Verify user exists
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Verify recipe exists
    recipe = db.query(Recipe).filter(Recipe.id == recipe_id).first()
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")

    # Find or create progress entry
    progress = (
        db.query(UserRecipeProgress)
        .filter(
            UserRecipeProgress.user_id == user_id,
            UserRecipeProgress.recipe_id == recipe_id,
            UserRecipeProgress.week_number == week_number,
        )
        .first()
    )

    if not progress:
        # Create new progress entry
        progress = UserRecipeProgress(
            user_id=user_id,
            recipe_id=recipe_id,
            week_number=week_number,
            status=status_update.status,
        )
        db.add(progress)
    else:
        # Update existing progress
        progress.status = status_update.status

        if status_update.status == "completed":
            progress.completed_at = datetime.now(timezone.utc)
        elif status_update.status == "in_progress":
            progress.completed_at = None
        elif status_update.status == "not_started":
            # Clear completion data when marking as incomplete
            progress.completed_at = None
            progress.feedback = None
            progress.notes = None
            progress.satisfaction_rating = None
            progress.difficulty_rating = None

    db.commit()
    db.refresh(progress)

    return progress
