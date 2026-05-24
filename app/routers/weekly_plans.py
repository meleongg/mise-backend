from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import List
from uuid import UUID
import uuid
from app.database import get_db
from app.utils.auth import get_current_user, require_same_user
from app.models import User, Recipe, WeeklyPlan
from app.schemas import WeeklyPlanResponse, RecipeResponse
from app.services.weekly_plan import WeeklyPlanService, parse_recipe_schedule

router = APIRouter()
plan_service = WeeklyPlanService()


@router.get("/weekly-plan", response_model=WeeklyPlanResponse)
async def get_weekly_plan(
    user_id: UUID = Query(..., description="User ID"),
    week_number: int = Query(None, description="Specific week number (optional)"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Get current week's recipes for a user"""
    require_same_user(current_user, user_id)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    # If no week specified, get current week
    if week_number is None:
        week_number = plan_service.get_current_week(user, db)

    # Get weekly plan
    plan = (
        db.query(WeeklyPlan)
        .filter(
            WeeklyPlan.user_id == user.id,
            WeeklyPlan.week_number == week_number,
        )
        .first()
    )

    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Weekly plan not found"
        )

    # Check if week is unlocked
    if not getattr(plan, "is_unlocked"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This week is not yet unlocked. Complete current week first.",
        )

    # Get recipe details
    recipe_ids = parse_recipe_schedule(getattr(plan, "recipe_schedule"))
    recipe_uuids = [uuid.UUID(rid) for rid in recipe_ids]
    recipes = db.query(Recipe).filter(Recipe.id.in_(recipe_uuids)).all()

    # Create response with recipes
    plan_response = WeeklyPlanResponse.model_validate(plan)
    plan_response.recipes = [
        RecipeResponse.model_validate(recipe) for recipe in recipes
    ]

    return plan_response


@router.get("/weekly-plan/{user_id}/all", response_model=List[WeeklyPlanResponse])
async def get_all_weekly_plans(
    user_id: UUID, db: Session = Depends(get_db), current_user=Depends(get_current_user)
):
    """Get all weekly plans for a user (for progress tracking)"""
    require_same_user(current_user, user_id)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    plans = db.query(WeeklyPlan).filter(WeeklyPlan.user_id == user_id).all()

    # Add recipe details to each plan
    response_plans = []
    for plan in plans:
        recipe_ids = parse_recipe_schedule(getattr(plan, "recipe_schedule"))
        recipe_uuids = [uuid.UUID(rid) for rid in recipe_ids]
        recipes = db.query(Recipe).filter(Recipe.id.in_(recipe_uuids)).all()

        plan_response = WeeklyPlanResponse.model_validate(plan)
        plan_response.recipes = [
            RecipeResponse.model_validate(recipe) for recipe in recipes
        ]
        response_plans.append(plan_response)

    return response_plans
