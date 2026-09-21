from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import PersonalRecipe, User
from app.schemas.sodie_proposals import PersonalRecipeResponse, UpdatePersonalRecipeRequest
from app.services import sodie_proposals as proposals
from app.utils.auth import get_current_user

router = APIRouter()


def _response(personal: PersonalRecipe) -> PersonalRecipeResponse:
    return PersonalRecipeResponse(**proposals.serialize_personal_recipe(personal))


@router.get("/personal-recipes", response_model=List[PersonalRecipeResponse])
def list_my_recipes(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    rows = (
        db.query(PersonalRecipe)
        .options(joinedload(PersonalRecipe.source_recipe))
        .filter(
            PersonalRecipe.user_id == current_user.id,
            PersonalRecipe.is_active.is_(True),
        )
        .order_by(PersonalRecipe.updated_at.desc())
        .all()
    )
    return [_response(item) for item in rows]


@router.get("/personal-recipes/{personal_recipe_id}", response_model=PersonalRecipeResponse)
def get_my_recipe(
    personal_recipe_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    personal = (
        db.query(PersonalRecipe)
        .options(joinedload(PersonalRecipe.source_recipe))
        .filter(
            PersonalRecipe.id == personal_recipe_id,
            PersonalRecipe.user_id == current_user.id,
        )
        .first()
    )
    if not personal:
        raise HTTPException(status_code=404, detail="Personal recipe not found")
    return _response(personal)


@router.patch(
    "/personal-recipes/{personal_recipe_id}", response_model=PersonalRecipeResponse
)
def update_my_recipe(
    personal_recipe_id: UUID,
    payload: UpdatePersonalRecipeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=422, detail="No fields to update")
    personal = proposals.update_personal_recipe(
        db, current_user, personal_recipe_id, **updates
    )
    # Reload with source for chrome fields
    return get_my_recipe(personal.id, db, current_user)


@router.delete(
    "/personal-recipes/{personal_recipe_id}", response_model=PersonalRecipeResponse
)
def archive_my_recipe(
    personal_recipe_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _response(
        proposals.archive_personal_recipe(db, current_user, personal_recipe_id)
    )
