from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    Text,
    ForeignKey,
    Boolean,
    TypeDecorator,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, TEXT
from pgvector.sqlalchemy import Vector
import uuid
from sqlalchemy.orm import relationship, declarative_base
from datetime import datetime, timezone

Base = declarative_base()


class GUID(TypeDecorator):
    """Platform-independent GUID type.

    Uses PostgreSQL's UUID type when available, otherwise uses String(36).
    """

    impl = String
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        else:
            return dialect.type_descriptor(String(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        elif dialect.name == "postgresql":
            return value
        else:
            return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        elif dialect.name == "postgresql":
            return value
        else:
            return uuid.UUID(value)


class User(Base):
    __tablename__ = "users"

    id = Column(GUID, primary_key=True, default=uuid.uuid4, index=True)
    email = Column(
        String(255), unique=True, nullable=False, index=True
    )  # unique email for login
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    cuisine = Column(String(50), nullable=False)  # preferred cuisine
    frequency = Column(Integer, nullable=False)  # meals per week
    skill_level = Column(String(20), nullable=False)  # beginner, intermediate, advanced
    user_goal = Column(
        String, nullable=False
    )  # e.g., "Learn New Techniques", "Master a Cuisine", etc.
    dietary_restrictions = Column(
        Text, nullable=True
    )  # JSON array of dietary restrictions (e.g., ["vegetarian", "gluten-free", "nut-free"])
    allergens = Column(
        Text, nullable=True
    )  # JSON array of allergens to avoid (e.g., ["nuts", "shellfish", "dairy"])
    preferred_portion_size = Column(
        String(50), nullable=True
    )  # Preferred serving size (e.g., "1-2", "3-4", "5-6", "family")
    max_prep_time_minutes = Column(
        Integer, nullable=True
    )  # Maximum acceptable prep time in minutes
    max_cook_time_minutes = Column(
        Integer, nullable=True
    )  # Maximum acceptable cook time in minutes
    recipe_repeat_preference = Column(
        String(20), nullable=False, default="standard"
    )  # standard (14d cooldown) or sooner (7d)
    city = Column(String(100), nullable=True)
    preferred_retailer = Column(String(100), nullable=True)
    sodie_memory_enabled = Column(Boolean, nullable=False, default=False)
    chat_retention_policy = Column(
        String(20), nullable=False, default="18_months"
    )
    hashed_password = Column(
        String, nullable=False
    )  # Store hashed password for authentication
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    weekly_plans = relationship("WeeklyPlan", back_populates="user")
    recipe_progress = relationship("UserRecipeProgress", back_populates="user")
    recipe_suggestions = relationship("RecipeSuggestion", back_populates="user")
    pantry_items = relationship(
        "UserPantryItem", back_populates="user", cascade="all, delete-orphan"
    )
    sodie_threads = relationship("SodieThread", back_populates="user", cascade="all, delete-orphan")
    personal_recipes = relationship(
        "PersonalRecipe", back_populates="user", cascade="all, delete-orphan"
    )
    sodie_action_proposals = relationship(
        "SodieActionProposal", back_populates="user", cascade="all, delete-orphan"
    )


class UserPantryItem(Base):
    __tablename__ = "user_pantry_items"

    id = Column(GUID, primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(GUID, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    is_baseline = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="pantry_items")


class SodieThread(Base):
    __tablename__ = "sodie_threads"
    id = Column(GUID, primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(GUID, ForeignKey("users.id"), nullable=False, index=True)
    scope = Column(String(20), nullable=False, default="global")
    context_id = Column(String(36), nullable=True)
    is_temporary = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    user = relationship("User", back_populates="sodie_threads")
    messages = relationship(
        "SodieMessage",
        back_populates="thread",
        cascade="all, delete-orphan",
        order_by="SodieMessage.created_at",
    )


class SodieMessage(Base):
    __tablename__ = "sodie_messages"
    id = Column(GUID, primary_key=True, default=uuid.uuid4, index=True)
    thread_id = Column(GUID, ForeignKey("sodie_threads.id"), nullable=False, index=True)
    sender = Column(String(10), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    thread = relationship("SodieThread", back_populates="messages")


class Recipe(Base):
    __tablename__ = "recipes"

    id = Column(GUID, primary_key=True, default=uuid.uuid4, index=True)
    external_id = Column(String(50), unique=True, index=True)  # TheMealDB ID
    name = Column(String(200), nullable=False)
    cuisine = Column(String(50), nullable=False)
    ingredients = Column(Text, nullable=False)  # JSON string of ingredients
    instructions = Column(Text, nullable=False)
    difficulty = Column(String(20), nullable=False)  # easy, medium, hard
    tags = Column(Text)  # JSON string of tags
    image_url = Column(String(500))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    recipe_progress = relationship("UserRecipeProgress", back_populates="recipe")
    recipe_suggestions = relationship("RecipeSuggestion", back_populates="recipe")

    # AI fields
    content_text = Column(TEXT, nullable=True)  # Full text for embedding
    embedding = Column(Vector(1536), nullable=True)  # Vector embedding
    is_ai_generated = Column(Boolean, default=False)  # Flag for AI origin

    # AI-augmented metadata fields
    dietary_tags = Column(
        Text, nullable=True
    )  # JSON array of dietary tags (e.g., ["vegetarian", "gluten-free"])
    allergens = Column(
        Text, nullable=True
    )  # JSON array of common allergens (e.g., ["nuts", "dairy"])
    portion_size = Column(
        String(50), nullable=True
    )  # Serving size (e.g., "4 servings", "6-8 people")
    prep_time_minutes = Column(Integer, nullable=True)  # Preparation time in minutes
    cook_time_minutes = Column(Integer, nullable=True)  # Cooking time in minutes
    skill_level_validated = Column(
        String(20), nullable=True
    )  # AI-validated skill level (beginner, medium, advanced)


class WeeklyPlan(Base):
    __tablename__ = "weekly_plans"

    id = Column(GUID, primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(GUID, ForeignKey("users.id"), nullable=False)
    week_number = Column(Integer, nullable=False)  # week of the course (1-N)
    recipe_schedule = Column(
        Text, nullable=False
    )  # JSON string of ordered recipes: [{"recipe_id": "uuid", "order": 0}, ...]
    swap_count = Column(Integer, default=0)  # Number of swaps used this week (max 3)
    generated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    is_unlocked = Column(Boolean, default=False)

    # Relationships
    user = relationship("User", back_populates="weekly_plans")


class UserRecipeProgress(Base):
    __tablename__ = "user_recipe_progress"

    id = Column(GUID, primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(GUID, ForeignKey("users.id"), nullable=False)
    recipe_id = Column(GUID, ForeignKey("recipes.id"), nullable=False)
    week_number = Column(Integer, nullable=False)
    status = Column(
        String(20), default="not_started"
    )  # not_started, in_progress, completed
    feedback = Column(String(20))  # too_easy, just_right, too_hard
    notes = Column(Text, nullable=True)
    completed_at = Column(DateTime)

    # Relationships
    user = relationship("User", back_populates="recipe_progress")
    recipe = relationship("Recipe", back_populates="recipe_progress")

    # Enhanced feedback for AI
    satisfaction_rating = Column(Integer, nullable=True)  # 1-5 rating
    difficulty_rating = Column(Integer, nullable=True)  # 1-5 rating


class RecipeSuggestion(Base):
    __tablename__ = "recipe_suggestions"

    id = Column(GUID, primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(GUID, ForeignKey("users.id"), nullable=False)
    recipe_id = Column(GUID, ForeignKey("recipes.id"), nullable=False)
    week_number = Column(Integer, nullable=False)
    source = Column(String(20), nullable=False)  # plan, swap_in, swap_out
    suggested_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    user = relationship("User", back_populates="recipe_suggestions")
    recipe = relationship("Recipe", back_populates="recipe_suggestions")


class PersonalRecipe(Base):
    __tablename__ = "personal_recipes"

    id = Column(GUID, primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(GUID, ForeignKey("users.id"), nullable=False, index=True)
    source_recipe_id = Column(GUID, ForeignKey("recipes.id"), nullable=True, index=True)
    name = Column(String(200), nullable=False)
    ingredients = Column(Text, nullable=False)
    instructions = Column(Text, nullable=False)
    portion_size = Column(String(50), nullable=True)
    notes = Column(Text, nullable=True)
    metadata_json = Column(Text, nullable=True)
    current_revision = Column(Integer, nullable=False, default=1)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="personal_recipes")
    source_recipe = relationship("Recipe")
    revisions = relationship(
        "PersonalRecipeRevision",
        back_populates="personal_recipe",
        cascade="all, delete-orphan",
    )


class PersonalRecipeRevision(Base):
    __tablename__ = "personal_recipe_revisions"

    id = Column(GUID, primary_key=True, default=uuid.uuid4, index=True)
    personal_recipe_id = Column(
        GUID, ForeignKey("personal_recipes.id"), nullable=False, index=True
    )
    revision_number = Column(Integer, nullable=False)
    content_snapshot = Column(Text, nullable=False)
    structured_diff = Column(Text, nullable=True)
    rationale = Column(Text, nullable=True)
    actor_user_id = Column(GUID, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    personal_recipe = relationship("PersonalRecipe", back_populates="revisions")


class SodieActionProposal(Base):
    __tablename__ = "sodie_action_proposals"

    id = Column(GUID, primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(GUID, ForeignKey("users.id"), nullable=False, index=True)
    thread_id = Column(GUID, ForeignKey("sodie_threads.id"), nullable=True, index=True)
    action_type = Column(String(50), nullable=False, default="propose_recipe_edit")
    status = Column(String(20), nullable=False, default="pending", index=True)
    source_recipe_id = Column(GUID, ForeignKey("recipes.id"), nullable=True)
    personal_recipe_id = Column(
        GUID, ForeignKey("personal_recipes.id"), nullable=True
    )
    payload_json = Column(Text, nullable=False)
    diff_json = Column(Text, nullable=False)
    impact_json = Column(Text, nullable=True)
    rationale = Column(Text, nullable=True)
    idempotency_key = Column(String(100), nullable=False)
    source_content_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    applied_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="sodie_action_proposals")
    thread = relationship("SodieThread")
    source_recipe = relationship("Recipe")
    personal_recipe = relationship("PersonalRecipe")
