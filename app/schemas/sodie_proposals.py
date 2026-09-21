"""Schemas for personal recipes and Sodie recipe-edit proposals."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


ALLOWLISTED_EDIT_FIELDS = (
    "title",
    "servings",
    "ingredients",
    "instructions",
    "notes",
    "metadata",
)


class RecipeEditPatch(BaseModel):
    """Allowlisted fields a Sodie proposal may change."""

    title: Optional[str] = Field(None, min_length=1, max_length=200)
    servings: Optional[str] = Field(None, max_length=50)
    ingredients: Optional[List[Any]] = None
    instructions: Optional[Any] = None
    notes: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class IngredientLine(BaseModel):
    """Normalized ingredient row for structured LLM output."""

    name: str = Field(..., min_length=1, max_length=200)
    measure: str = Field("", max_length=100)


class RecipeEditPatchDraft(BaseModel):
    """
    Structured LLM output for a recipe edit.

    Only include fields that must change. When changing ingredients, return the
    full updated list (not a delta). Never dump the user request into notes.
    """

    title: Optional[str] = Field(None, min_length=1, max_length=200)
    servings: Optional[str] = Field(None, max_length=50)
    ingredients: Optional[List[IngredientLine]] = None
    instructions: Optional[Any] = None
    notes: Optional[str] = Field(
        None,
        description="Cook tip that belongs on the recipe card — never the raw user request.",
    )
    ambiguous: bool = Field(
        False,
        description="True when the request cannot be mapped to a concrete field change.",
    )
    change_summary: str = Field(
        ...,
        min_length=1,
        max_length=300,
        description="Short description of what changed for the proposal rationale.",
    )


class ProposeRecipeEditRequest(BaseModel):
    source_recipe_id: UUID
    patch: RecipeEditPatch
    rationale: Optional[str] = Field(None, max_length=2000)
    idempotency_key: str = Field(..., min_length=8, max_length=100)
    thread_id: Optional[UUID] = None


class ProposeRecipeEditFromRequest(BaseModel):
    """Natural-language edit; server builds an allowlisted patch via structured LLM."""

    source_recipe_id: UUID
    request: str = Field(..., min_length=1, max_length=2000)
    idempotency_key: str = Field(..., min_length=8, max_length=100)
    thread_id: Optional[UUID] = None


class ClarifyProposalRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)


class PersonalRecipeResponse(BaseModel):
    id: UUID
    source_recipe_id: Optional[UUID] = None
    name: str
    ingredients: Any
    instructions: Any
    portion_size: Optional[str] = None
    notes: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    current_revision: int
    is_active: bool
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class SodieActionProposalResponse(BaseModel):
    id: UUID
    thread_id: Optional[UUID] = None
    action_type: str
    status: Literal["pending", "approved", "rejected", "expired", "applied"]
    source_recipe_id: Optional[UUID] = None
    personal_recipe_id: Optional[UUID] = None
    payload: Dict[str, Any]
    diff: Dict[str, Any]
    impact: Dict[str, Any]
    rationale: Optional[str] = None
    idempotency_key: str
    created_at: datetime
    updated_at: datetime
    applied_at: Optional[datetime] = None
    model_config = {"from_attributes": True}


class ProposeRecipeEditResponse(BaseModel):
    proposal: SodieActionProposalResponse
    assistant_message: Optional[str] = None
