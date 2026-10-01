from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Literal, Any, Dict
from datetime import datetime
from uuid import UUID
import json

# --- Password policy ---
# Standard baseline aligned with NIST 800-63B guidance: length is the primary
# strength factor; we additionally require a letter + digit for resilience
# against trivial passwords. All characters (incl. spaces, symbols, unicode)
# are permitted so users can pick passphrases and password-manager output.
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128
PASSWORD_REQUIREMENTS_MESSAGE = (
    f"Password must be {PASSWORD_MIN_LENGTH}-{PASSWORD_MAX_LENGTH} characters and "
    "include at least one letter and one number."
)


def validate_password_strength(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Password must be a string")
    if value != value.strip():
        raise ValueError("Password cannot start or end with whitespace")
    length = len(value)
    if length < PASSWORD_MIN_LENGTH or length > PASSWORD_MAX_LENGTH:
        raise ValueError(PASSWORD_REQUIREMENTS_MESSAGE)
    if not any(ch.isalpha() for ch in value):
        raise ValueError(PASSWORD_REQUIREMENTS_MESSAGE)
    if not any(ch.isdigit() for ch in value):
        raise ValueError(PASSWORD_REQUIREMENTS_MESSAGE)
    return value


# Auth schemas
class LoginRequest(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _normalize_login_email(cls, value: str) -> str:
        return value.strip().lower()


# User schemas
class UserCreate(BaseModel):
    cuisine: str = Field(..., min_length=1, max_length=50)
    frequency: int = Field(..., ge=1, le=7)  # 1-7 meals per week
    skill_level: str = Field(..., pattern="^(beginner|intermediate|advanced)$")
    user_goal: str = Field(
        ..., description="e.g., 'Learn New Techniques', 'Master a Cuisine', etc."
    )
    dietary_restrictions: Optional[str] = Field(
        None,
        description="JSON array of dietary restrictions (e.g., ['vegetarian', 'gluten-free'])",
    )
    allergens: Optional[str] = Field(
        None,
        description="JSON array of allergens to avoid (e.g., ['nuts', 'shellfish'])",
    )
    preferred_portion_size: Optional[str] = Field(
        None,
        max_length=50,
        description="Preferred serving size (e.g., '2-3', '4', 'family')",
    )
    max_prep_time_minutes: Optional[int] = Field(
        None, ge=0, description="Maximum acceptable prep time in minutes"
    )
    max_cook_time_minutes: Optional[int] = Field(
        None, ge=0, description="Maximum acceptable cook time in minutes"
    )
    city: Optional[str] = Field(None, max_length=100)
    preferred_retailer: Optional[str] = Field(None, max_length=100)
    sodie_memory_enabled: Optional[bool] = None
    chat_retention_policy: Optional[Literal["3_months", "18_months", "36_months", "manual"]] = None


class UserUpdate(BaseModel):
    cuisine: Optional[str] = Field(None, min_length=1, max_length=50)
    frequency: Optional[int] = Field(None, ge=1, le=7)
    skill_level: Optional[str] = Field(
        None, pattern="^(beginner|intermediate|advanced)$"
    )
    user_goal: Optional[str] = Field(
        None, description="e.g., 'Learn New Techniques', 'Master a Cuisine', etc."
    )
    dietary_restrictions: Optional[str] = Field(
        None, description="JSON array of dietary restrictions"
    )
    allergens: Optional[str] = Field(
        None, description="JSON array of allergens to avoid"
    )
    preferred_portion_size: Optional[str] = Field(
        None, max_length=50, description="Preferred serving size"
    )
    max_prep_time_minutes: Optional[int] = Field(
        None, ge=0, description="Maximum acceptable prep time"
    )
    max_cook_time_minutes: Optional[int] = Field(
        None, ge=0, description="Maximum acceptable cook time"
    )
    recipe_repeat_preference: Optional[str] = Field(
        None,
        pattern="^(standard|sooner)$",
        description="How long before a cooked recipe can reappear in plans",
    )
    city: Optional[str] = Field(None, max_length=100)
    preferred_retailer: Optional[str] = Field(None, max_length=100)
    sodie_memory_enabled: Optional[bool] = None
    chat_retention_policy: Optional[Literal["3_months", "18_months", "36_months", "manual"]] = None


class PantryItemInput(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    is_baseline: bool = False

    @field_validator("name")
    @classmethod
    def _trim_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Pantry item cannot be empty")
        return value


class PantryItemResponse(PantryItemInput):
    id: UUID

    model_config = {"from_attributes": True}


class PantryReplaceRequest(BaseModel):
    items: List[PantryItemInput] = Field(default_factory=list, max_length=100)


class SodieThreadCreate(BaseModel):
    scope: Literal[
        "global",
        "plan",
        "recipe",
        "kitchen",
        "shopping",
        "personal_recipe",
        "settings",
        "analytics",
    ] = "global"
    # Entity id for the page: catalog/personal recipe UUID, weekly_plan UUID,
    # or week number as digits. Unused for settings/analytics.
    context_id: Optional[str] = Field(None, max_length=36)
    is_temporary: bool = False


class SodieKitchenTimer(BaseModel):
    """Client-reported countdown for kitchen coach context."""

    label: str = Field(..., min_length=1, max_length=80)
    remaining_seconds: int = Field(..., ge=0, le=24 * 60 * 60)


class SodieKitchenState(BaseModel):
    """Live Kitchen Mode progress for coach prompts (not persisted)."""

    current_step_index: int = Field(..., ge=0, le=200)
    total_steps: int = Field(..., ge=0, le=200)
    current_step_text: Optional[str] = Field(None, max_length=500)
    checked_ingredients: int = Field(0, ge=0, le=500)
    total_ingredients: int = Field(0, ge=0, le=500)
    active_timers: List[SodieKitchenTimer] = Field(default_factory=list, max_length=8)
    read_aloud_active: bool = False


class SodieMessageCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)
    kitchen_state: Optional[SodieKitchenState] = None


class SodieMessageResponse(BaseModel):
    id: UUID
    sender: Literal["user", "ai"]
    content: str
    created_at: datetime
    model_config = {"from_attributes": True}


class SodieThreadResponse(BaseModel):
    id: UUID
    scope: str
    context_id: Optional[str]
    is_temporary: bool
    created_at: datetime
    updated_at: datetime
    messages: List[SodieMessageResponse] = []
    model_config = {"from_attributes": True}


class SodieChatResponse(BaseModel):
    user_message: SodieMessageResponse
    ai_message: SodieMessageResponse
    proposal: Optional[Any] = None


class UpdateAccountDetails(BaseModel):
    """Schema for updating account details (email, name)"""

    email: Optional[str] = Field(None, description="User's email address")
    first_name: Optional[str] = Field(None, min_length=1, max_length=100)
    last_name: Optional[str] = Field(None, min_length=1, max_length=100)


class ChangePasswordRequest(BaseModel):
    """Schema for changing user password"""

    current_password: str = Field(..., min_length=1)
    new_password: str = Field(
        ...,
        min_length=PASSWORD_MIN_LENGTH,
        max_length=PASSWORD_MAX_LENGTH,
        description=PASSWORD_REQUIREMENTS_MESSAGE,
    )

    @field_validator("new_password")
    @classmethod
    def _validate_new_password(cls, value: str) -> str:
        return validate_password_strength(value)


class MessageResponse(BaseModel):
    """Generic message response"""

    message: str


class UserResponse(BaseModel):
    id: UUID
    email: str
    first_name: str
    last_name: str
    cuisine: str
    frequency: int
    skill_level: str
    user_goal: str
    dietary_restrictions: Optional[str] = None
    allergens: Optional[str] = None
    preferred_portion_size: Optional[str] = None
    max_prep_time_minutes: Optional[int] = None
    max_cook_time_minutes: Optional[int] = None
    recipe_repeat_preference: str = "standard"
    city: Optional[str] = None
    preferred_retailer: Optional[str] = None
    sodie_memory_enabled: bool = False
    chat_retention_policy: str = "18_months"
    created_at: datetime

    model_config = {"from_attributes": True}


# Registration request/response schemas
class RegisterRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=320)
    password: str = Field(
        ...,
        min_length=PASSWORD_MIN_LENGTH,
        max_length=PASSWORD_MAX_LENGTH,
        description=PASSWORD_REQUIREMENTS_MESSAGE,
    )
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized or "." not in normalized.split("@")[-1]:
            raise ValueError("Enter a valid email address")
        return normalized

    @field_validator("first_name", "last_name")
    @classmethod
    def _trim_name(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("Name cannot be empty")
        return trimmed

    @field_validator("password")
    @classmethod
    def _validate_password(cls, value: str) -> str:
        return validate_password_strength(value)


class RegisterResponse(BaseModel):
    success: bool
    message: str
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    user: Optional["UserResponse"] = None


# Token response for login
class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserResponse

    model_config = {"from_attributes": True}


class AccessTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


# Recipe schemas
class RecipeResponse(BaseModel):
    id: UUID
    external_id: str
    name: str
    cuisine: str
    ingredients: str
    instructions: str
    difficulty: str
    tags: Optional[str]
    image_url: Optional[str]
    dietary_tags: Optional[str] = None
    allergens: Optional[str] = None
    portion_size: Optional[str] = None
    prep_time_minutes: Optional[int] = None
    cook_time_minutes: Optional[int] = None
    skill_level_validated: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# Weekly Plan schemas
class WeeklyPlanEntryResponse(BaseModel):
    id: UUID
    weekly_plan_id: UUID
    position: int
    catalog_recipe_id: Optional[UUID] = None
    personal_recipe_id: Optional[UUID] = None
    recipe_snapshot: Dict[str, Any] = {}
    selected_servings: Optional[str] = None
    lifecycle_state: str = "planned"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}

    @field_validator("recipe_snapshot", mode="before")
    @classmethod
    def _parse_recipe_snapshot(cls, value: Any) -> Dict[str, Any]:
        if value is None or value == "":
            return {}
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}


class GenerationSummary(BaseModel):
    verification_run_id: str
    attempt_number: int = 1
    auto_repaired: bool = False
    confidence: str
    confidence_reasons: List[str] = []


class PrepTimelineItemResponse(BaseModel):
    kind: str  # shop | advance_prep | cook
    title: str
    detail: str
    day_label: str
    duration_minutes: Optional[int] = None
    recipe_id: Optional[str] = None
    reasons: List[str] = []


class PrepTimelineResponse(BaseModel):
    kind: str = "deterministic_v1"
    total_active_minutes: int = 0
    notes: List[str] = []
    items: List[PrepTimelineItemResponse] = []
    source: Optional[str] = None  # snapshot | computed
    snapshotted_at: Optional[datetime] = None


class WeeklyPlanResponse(BaseModel):
    id: UUID
    user_id: UUID
    week_number: int
    recipe_schedule: str
    generated_at: datetime
    is_unlocked: bool
    recipes: List[RecipeResponse] = []
    entries: List[WeeklyPlanEntryResponse] = []
    swap_count: int = 0
    generation_summary: Optional[GenerationSummary] = None
    prep_timeline: Optional[PrepTimelineResponse] = None

    model_config = {"from_attributes": True}


# Feedback schemas
class FeedbackCreate(BaseModel):
    recipe_id: UUID
    week_number: int
    feedback: str = Field(..., pattern="^(too_easy|just_right|too_hard)$")
    notes: Optional[str] = Field(None, max_length=2000)


class UpdateRecipeStatus(BaseModel):
    status: Literal["not_started", "in_progress", "completed"]


class UserRecipeProgressResponse(BaseModel):
    id: UUID
    user_id: UUID
    recipe_id: UUID
    week_number: int
    status: str
    feedback: Optional[str]
    notes: Optional[str] = None
    completed_at: Optional[datetime]


# Progress summary schema
class ProgressSummary(BaseModel):
    total_recipes: int
    completed_recipes: int
    current_week: int
    completion_rate: float
    skill_progression: str


class PlanGenerationInput(BaseModel):
    initial_intent: str = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="User intent for weekly plan generation",
    )
    confirm_regeneration: bool = Field(
        False,
        description="Explicit acknowledgement that an existing week 1 plan will be reset",
    )


class GeneralChatInput(BaseModel):
    user_message: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Message to Sodie coach chat",
    )
    week_number: Optional[int] = None


class SwapRecipeRequest(BaseModel):
    recipe_id_to_replace: UUID
    week_number: Optional[int] = Field(
        None, description="Week number to modify (defaults to most recent)"
    )
    swap_context: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Reason for swap and desired replacement characteristics",
    )


class SwapRecipeResponse(BaseModel):
    success: bool
    old_recipe: RecipeResponse
    new_recipe: RecipeResponse
    message: str


class ShoppingListItemSourceResponse(BaseModel):
    id: UUID
    shopping_list_item_id: UUID
    weekly_plan_entry_id: UUID
    source_amount: Optional[str] = None
    inclusion_state: str = "included"

    model_config = {"from_attributes": True}


class ShoppingListItemResponse(BaseModel):
    id: UUID
    shopping_list_id: UUID
    normalized_name: str
    display_text: str
    quantity: Optional[float] = None
    unit: Optional[str] = None
    aisle: Optional[str] = None
    is_checked: bool = False
    is_user_edit: bool = False
    needs_review: bool = False
    omitted_by_pantry: bool = False
    pantry_omit_confirmed_at: Optional[datetime] = None
    pantry_match: bool = False
    confidence: Optional[str] = None
    reason: Optional[str] = None
    sort_order: int = 0
    sources: List[ShoppingListItemSourceResponse] = []
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class ShoppingListResponse(BaseModel):
    id: UUID
    user_id: UUID
    weekly_plan_id: Optional[UUID] = None
    title: str
    status: str
    retailer_snapshot: Optional[str] = None
    location_snapshot: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    archived_at: Optional[datetime] = None
    items: List[ShoppingListItemResponse] = []

    model_config = {"from_attributes": True}


class GenerateShoppingListRequest(BaseModel):
    week_number: int = Field(..., ge=1)


class UpdateShoppingListItemRequest(BaseModel):
    is_checked: Optional[bool] = None
    display_text: Optional[str] = Field(None, min_length=1, max_length=300)
    quantity: Optional[float] = None
    unit: Optional[str] = Field(None, max_length=50)
    omitted_by_pantry: Optional[bool] = None
    confirm_pantry_omit: Optional[bool] = None


class ShoppingCheckSyncItem(BaseModel):
    item_id: UUID
    is_checked: bool
    client_updated_at: datetime


class ShoppingCheckSyncRequest(BaseModel):
    updates: List[ShoppingCheckSyncItem] = Field(..., min_length=1, max_length=200)


class UpdatePlanEntryServingsRequest(BaseModel):
    selected_servings: Optional[str] = Field(None, max_length=50)
