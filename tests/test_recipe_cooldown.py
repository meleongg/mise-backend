from datetime import datetime, timedelta, timezone
import uuid

from app.models import Recipe, RecipeSuggestion, User
from app.services.weekly_plan import WeeklyPlanService


def _seed_recipe(db, recipe_id: uuid.UUID) -> None:
    db.add(
        Recipe(
            id=recipe_id,
            external_id=str(recipe_id),
            name="Cooldown Test Recipe",
            cuisine="Italian",
            ingredients='["salt"]',
            instructions="Cook",
            difficulty="easy",
            tags='["test"]',
            image_url="https://example.com/image.jpg",
        )
    )
    db.flush()


def test_get_user_exclusion_ids_respects_sooner_preference(db):
    service = WeeklyPlanService()
    user = User(
        id=uuid.uuid4(),
        email="cooldown@example.com",
        first_name="Cool",
        last_name="Down",
        cuisine="Italian",
        frequency=3,
        skill_level="beginner",
        user_goal="confidence",
        hashed_password="hash",
        recipe_repeat_preference="sooner",
    )
    db.add(user)
    db.flush()

    recent_recipe_id = uuid.uuid4()
    old_recipe_id = uuid.uuid4()
    _seed_recipe(db, recent_recipe_id)
    _seed_recipe(db, old_recipe_id)
    now = datetime.now(timezone.utc)

    db.add(
        RecipeSuggestion(
            user_id=user.id,
            recipe_id=recent_recipe_id,
            week_number=1,
            source="plan",
            suggested_at=now - timedelta(days=3),
        )
    )
    db.add(
        RecipeSuggestion(
            user_id=user.id,
            recipe_id=old_recipe_id,
            week_number=1,
            source="plan",
            suggested_at=now - timedelta(days=10),
        )
    )
    db.flush()

    exclusion_ids = service.get_user_exclusion_ids(user, db)

    assert recent_recipe_id in exclusion_ids
    assert old_recipe_id not in exclusion_ids


def test_get_user_exclusion_ids_uses_standard_preference_by_default(db):
    service = WeeklyPlanService()
    user = User(
        id=uuid.uuid4(),
        email="standard@example.com",
        first_name="Standard",
        last_name="User",
        cuisine="Italian",
        frequency=3,
        skill_level="beginner",
        user_goal="confidence",
        hashed_password="hash",
        recipe_repeat_preference="standard",
    )
    db.add(user)
    db.flush()

    borderline_recipe_id = uuid.uuid4()
    _seed_recipe(db, borderline_recipe_id)
    now = datetime.now(timezone.utc)

    db.add(
        RecipeSuggestion(
            user_id=user.id,
            recipe_id=borderline_recipe_id,
            week_number=1,
            source="plan",
            suggested_at=now - timedelta(days=10),
        )
    )
    db.flush()

    exclusion_ids = service.get_user_exclusion_ids(user, db)

    assert borderline_recipe_id in exclusion_ids
