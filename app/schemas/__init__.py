from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Literal
from datetime import datetime
from uuid import UUID

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
class WeeklyPlanResponse(BaseModel):
    id: UUID
    user_id: UUID
    week_number: int
    recipe_schedule: str
    generated_at: datetime
    is_unlocked: bool
    recipes: List[RecipeResponse] = []
    swap_count: int = 0

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
    initial_intent: str


class GeneralChatInput(BaseModel):
    user_message: str


class AdaptiveChatResponse(BaseModel):
    response: str
    intent: str


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
