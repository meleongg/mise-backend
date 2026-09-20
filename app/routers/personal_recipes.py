from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.schemas.sodie_proposals import PersonalRecipeResponse
from app.services import sodie_proposals as proposals
from app.utils.auth import get_current_user

router = APIRouter()


@router.get("/personal-recipes", response_model=List[PersonalRecipeResponse])
def list_my_recipes(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    return [
        PersonalRecipeResponse(**proposals.serialize_personal_recipe(item))
        for item in proposals.list_personal_recipes(db, current_user)
    ]


@router.get("/personal-recipes/{personal_recipe_id}", response_model=PersonalRecipeResponse)
def get_my_recipe(
    personal_recipe_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return PersonalRecipeResponse(
        **proposals.serialize_personal_recipe(
            proposals.get_personal_recipe(db, current_user, personal_recipe_id)
        )
    )
