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


class InstructionStep(BaseModel):
    """Normalized instruction step for structured LLM output (OpenAI requires typed fields)."""

    step: Optional[int] = Field(None, ge=1)
    text: str = Field(..., min_length=1, max_length=2000)


class RecipeEditPatchDraft(BaseModel):
    """
    Structured LLM classification + patch for a recipe-edit follow-up.

    The model owns intent routing (propose vs clarify vs ask for more info) and
    field routing (ingredients vs servings vs notes, etc.). Never dump the raw
    user request into notes.
    """

    intent: Literal["propose_edit", "clarify", "needs_more_info"] = Field(
        ...,
        description=(
            "propose_edit = build a new allowlisted patch; "
            "clarify = answer a question about a pending diff without changing it; "
            "needs_more_info = cannot map the ask to a concrete change yet."
        ),
    )
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    servings: Optional[str] = Field(None, max_length=50)
    ingredients: Optional[List[IngredientLine]] = Field(
        None,
        description=(
            "COMPLETE ingredient list for the recipe when any ingredient changes: "
            "keep every existing row, update only requested measures, append new rows. "
            "Never return only the changed ingredient."
        ),
    )
    instructions: Optional[List[InstructionStep]] = Field(
        None,
        description="Full updated instruction list when steps change; omit if unchanged.",
    )
    notes: Optional[str] = Field(
        None,
        description="Cook tip that belongs on the recipe card — never the raw user request.",
    )
    change_summary: str = Field(
        ...,
        min_length=1,
        max_length=300,
        description="Short rationale for the proposal, or why clarify/needs_more_info.",
    )
    assistant_reply: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Short user-facing reply for clarify / needs_more_info (and optional intro for propose_edit).",
    )


class ProposeRecipeEditRequest(BaseModel):
    source_recipe_id: UUID
    patch: RecipeEditPatch
    rationale: Optional[str] = Field(None, max_length=2000)
    idempotency_key: str = Field(..., min_length=8, max_length=100)
    thread_id: Optional[UUID] = None


class ProposeRecipeEditFromRequest(BaseModel):
    """Natural-language edit; server classifies intent and builds an allowlisted patch."""

    source_recipe_id: UUID
    request: str = Field(..., min_length=1, max_length=2000)
    idempotency_key: str = Field(..., min_length=8, max_length=100)
    thread_id: Optional[UUID] = None
    pending_proposal_id: Optional[UUID] = None


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
    proposal: Optional[SodieActionProposalResponse] = None
    kind: Literal["proposal", "clarify", "needs_more_info"] = "proposal"
    assistant_message: Optional[str] = None
