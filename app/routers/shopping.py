from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.schemas import (
    GenerateShoppingListRequest,
    ShoppingCheckSyncRequest,
    ShoppingListResponse,
    UpdateShoppingListItemRequest,
)
from app.services import shopping as shopping_service
from app.utils.auth import get_current_user

router = APIRouter()


def _list_response(db: Session, user: User, shopping_list) -> ShoppingListResponse:
    pantry = shopping_service.pantry_normalized_names(db, user.id)
    return ShoppingListResponse.model_validate(
        shopping_service.serialize_shopping_list(
            shopping_list, pantry_names=pantry
        )
    )


@router.get("/shopping-lists/active", response_model=Optional[ShoppingListResponse])
def get_active_shopping_list(
    week_number: Optional[int] = Query(None, ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    shopping_list = shopping_service.get_active_shopping_list(
        db, current_user, week_number=week_number
    )
    if not shopping_list:
        return None
    return _list_response(db, current_user, shopping_list)


@router.post("/shopping-lists/generate", response_model=ShoppingListResponse)
def generate_shopping_list(
    payload: GenerateShoppingListRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    shopping_list = shopping_service.generate_or_refresh_shopping_list(
        db, current_user, payload.week_number
    )
    return _list_response(db, current_user, shopping_list)


@router.post(
    "/shopping-lists/sync-checks", response_model=ShoppingListResponse
)
def sync_shopping_checks(
    payload: ShoppingCheckSyncRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Flush queued offline check toggles (last client_updated_at per item wins)."""
    shopping_list = shopping_service.sync_shopping_check_states(
        db,
        current_user,
        [item.model_dump() for item in payload.updates],
    )
    return _list_response(db, current_user, shopping_list)


@router.patch(
    "/shopping-lists/items/{item_id}", response_model=ShoppingListResponse
)
def patch_shopping_list_item(
    item_id: UUID,
    payload: UpdateShoppingListItemRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=422, detail="No fields to update")
    shopping_list = shopping_service.update_shopping_list_item(
        db, current_user, item_id, **updates
    )
    return _list_response(db, current_user, shopping_list)
