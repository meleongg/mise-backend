from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from uuid import UUID

from app.database import get_db
from app.models import Recipe, User
from app.schemas import RecipeResponse
from app.services.weekly_plan import (
    find_bound_plan_entry_for_catalog,
    recipe_response_with_optional_entry_overlay,
)
from app.utils.auth import get_current_user

router = APIRouter()


@router.get("/recipe/{recipe_id}", response_model=RecipeResponse)
async def get_recipe(
    recipe_id: UUID,
    week_number: Optional[int] = Query(
        None,
        ge=1,
        description="When set, overlay personally bound plan-entry snapshot for that week",
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get recipe details by catalog ID, optionally as the plan-bound personal view."""
    recipe = db.query(Recipe).filter(Recipe.id == recipe_id).first()
    if not recipe:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Recipe not found"
        )

    entry = None
    if week_number is not None:
        entry = find_bound_plan_entry_for_catalog(
            db, current_user, recipe_id, int(week_number)
        )
    return recipe_response_with_optional_entry_overlay(recipe, entry)
