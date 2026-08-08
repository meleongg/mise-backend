import uuid
import json
from typing import List
from sqlalchemy.orm import Session
from app.models import User, WeeklyPlan, UserRecipeProgress, Recipe, RecipeSuggestion
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from fastapi import HTTPException
from app.constants import get_recipe_cooldown_days


# Recipe Schedule Helper Functions
def create_recipe_schedule(recipe_ids: List[str]) -> str:
    """
    Convert a list of recipe IDs to an ordered recipe schedule JSON string.

    Args:
        recipe_ids: List of recipe ID strings in desired order

    Returns:
        JSON string of ordered recipe schedule: [{"recipe_id": "uuid", "order": 0}, ...]
    """
    schedule = [{"recipe_id": rid, "order": idx} for idx, rid in enumerate(recipe_ids)]
    return json.dumps(schedule)


def parse_recipe_schedule(recipe_schedule_json: str) -> List[str]:
    """
    Parse a recipe schedule JSON string to extract ordered recipe IDs.

    Args:
        recipe_schedule_json: JSON string of recipe schedule

    Returns:
        List of recipe ID strings in order
    """
    schedule = json.loads(recipe_schedule_json)
    # Sort by order field to ensure correct sequence
    sorted_schedule = sorted(schedule, key=lambda x: x.get("order", 0))
    return [item["recipe_id"] for item in sorted_schedule]


def swap_recipe_in_schedule(recipe_schedule_json: str, old_id: str, new_id: str) -> str:
    """
    Swap a recipe in the schedule while maintaining order positions.

    Args:
        recipe_schedule_json: JSON string of recipe schedule
        old_id: UUID of recipe to remove
        new_id: UUID of recipe to add in its place

    Returns:
        Updated JSON string with new recipe at same position
    """
    schedule = json.loads(recipe_schedule_json)

    # Find and replace the recipe with matching ID
    for item in schedule:
        if item["recipe_id"] == old_id:
            item["recipe_id"] = new_id
            break

    return json.dumps(schedule)


class WeeklyPlanService:
    def load_recipes_for_plan(self, plan: WeeklyPlan, db: Session) -> WeeklyPlan:
        """Load the full Recipe objects for a WeeklyPlan and attach them as a property."""
        recipe_ids = parse_recipe_schedule(plan.recipe_schedule)
        recipe_uuids = [uuid.UUID(rid) for rid in recipe_ids]

        # Query all recipes in the correct order
        recipes = db.query(Recipe).filter(Recipe.id.in_(recipe_uuids)).all()

        # Sort recipes to match the order in recipe_ids
        recipes_dict = {str(r.id): r for r in recipes}
        ordered_recipes = [
            recipes_dict[rid] for rid in recipe_ids if rid in recipes_dict
        ]

        # Attach as a dynamic attribute (Pydantic will pick it up)
        plan.recipes = ordered_recipes
        return plan

    def get_user_exclusion_ids(
        self, user: User, db: Session, current_week: int = None
    ) -> List[uuid.UUID]:
        """
        Fetches all recipe IDs to exclude from the next plan.
        Includes:
        1. Recipes rated as too difficult (difficulty_rating >= 4)
        2. Recipes suggested within the cooldown window (plan + swap)
        3. Recipes completed within the cooldown window
        """
        exclusion_ids = []

        hard_recipes = db.scalars(
            select(UserRecipeProgress.recipe_id).filter(
                (UserRecipeProgress.user_id == user.id)
                & (UserRecipeProgress.difficulty_rating >= 4)
            )
        ).all()
        exclusion_ids.extend(hard_recipes)

        cooldown_days = get_recipe_cooldown_days(
            getattr(user, "recipe_repeat_preference", None)
        )
        cooldown_cutoff = datetime.now(timezone.utc) - timedelta(days=cooldown_days)
        recent_suggestions = db.scalars(
            select(RecipeSuggestion.recipe_id).filter(
                (RecipeSuggestion.user_id == user.id)
                & (RecipeSuggestion.suggested_at >= cooldown_cutoff)
            )
        ).all()
        exclusion_ids.extend(recent_suggestions)

        # Completion history is authoritative even when a plan predates
        # RecipeSuggestion tracking or its suggestion row is unavailable.
        # This keeps a just-completed dish out of both generated plans and swaps.
        recent_completions = db.scalars(
            select(UserRecipeProgress.recipe_id).filter(
                (UserRecipeProgress.user_id == user.id)
                & (UserRecipeProgress.status == "completed")
                & (UserRecipeProgress.completed_at.isnot(None))
                & (UserRecipeProgress.completed_at >= cooldown_cutoff)
            )
        ).all()
        exclusion_ids.extend(recent_completions)

        # Remove duplicates and return
        return list(set(exclusion_ids))

    def _create_progress_entries(
        self,
        user_id: uuid.UUID,
        week_number: int,
        recipe_ids: List[uuid.UUID],
        db: Session,
    ) -> None:
        """
        Helper method to create UserRecipeProgress entries for a weekly plan.
        Creates entries with status='not_started' for each recipe.
        """
        for recipe_id in recipe_ids:
            progress_entry = UserRecipeProgress(
                user_id=user_id,
                recipe_id=recipe_id,
                week_number=week_number,
                status="not_started",
                completed_at=None,
            )
            db.add(progress_entry)

    def record_recipe_suggestions(
        self,
        user_id: uuid.UUID,
        week_number: int,
        recipe_ids: List[uuid.UUID],
        source: str,
        db: Session,
        clear_existing: bool = False,
    ) -> None:
        if clear_existing:
            db.query(RecipeSuggestion).filter(
                RecipeSuggestion.user_id == user_id,
                RecipeSuggestion.week_number == week_number,
                RecipeSuggestion.source == source,
            ).delete(synchronize_session=False)

        for recipe_id in recipe_ids:
            suggestion = RecipeSuggestion(
                user_id=user_id,
                recipe_id=recipe_id,
                week_number=week_number,
                source=source,
                suggested_at=datetime.now(timezone.utc),
            )
            db.add(suggestion)

    def get_current_week(self, user: User, db: Session) -> int:
        """Get the current week number for the user based on their progress"""
        # Get the latest completed week
        latest_progress = (
            db.query(UserRecipeProgress)
            .filter(
                UserRecipeProgress.user_id == user.id,
                UserRecipeProgress.status == "completed",
            )
            .order_by(UserRecipeProgress.week_number.desc())
            .first()
        )

        if not latest_progress:
            return 1  # First week

        # Check if all recipes in the latest week are completed
        week_recipes = (
            db.query(UserRecipeProgress)
            .filter(
                UserRecipeProgress.user_id == user.id,
                UserRecipeProgress.week_number == latest_progress.week_number,
            )
            .all()
        )

        completed_recipes = [
            p for p in week_recipes if getattr(p, "status") == "completed"
        ]

        if len(completed_recipes) == len(week_recipes) and len(week_recipes) > 0:
            # All recipes completed, move to next week
            next_week = getattr(latest_progress, "week_number") + 1
            return next_week
        else:
            return getattr(latest_progress, "week_number")

    async def generate_weekly_plan(
        self,
        user: User,
        week_number: int,
        recipe_ids_from_agent: List[uuid.UUID],
        db: Session,
    ) -> WeeklyPlan:
        """
        Commits a new weekly plan to the database using the recipe list provided
        by the Adaptive Planner Agent.
        Also creates UserRecipeProgress entries for each recipe in the plan.
        """

        # Create the recipe schedule with ordered recipes
        recipe_schedule_str = create_recipe_schedule(
            [str(uid) for uid in recipe_ids_from_agent]
        )

        existing_plan = (
            db.query(WeeklyPlan)
            .filter(
                WeeklyPlan.user_id == user.id, WeeklyPlan.week_number == week_number
            )
            .first()
        )
        if existing_plan:
            # Update the existing plan's recipes with the agent's new list
            existing_plan.recipe_schedule = recipe_schedule_str
            db.add(existing_plan)

            # Delete old progress entries for this week and create new ones
            db.query(UserRecipeProgress).filter(
                UserRecipeProgress.user_id == user.id,
                UserRecipeProgress.week_number == week_number,
            ).delete()

            # Create progress entries for new recipe list
            self._create_progress_entries(
                user.id, week_number, recipe_ids_from_agent, db
            )

            # Record suggestions for cooldown tracking
            self.record_recipe_suggestions(
                user_id=user.id,
                week_number=week_number,
                recipe_ids=recipe_ids_from_agent,
                source="plan",
                db=db,
                clear_existing=True,
            )

            db.commit()
            db.refresh(existing_plan)

            # Load the full recipe objects before returning
            return self.load_recipes_for_plan(existing_plan, db)

        # Create the new plan record
        new_plan = WeeklyPlan(
            user_id=user.id,
            week_number=week_number,
            recipe_schedule=recipe_schedule_str,
            generated_at=datetime.now(timezone.utc),
            is_unlocked=True,  # Auto-unlock the first plan
        )

        # Commit the plan first
        db.add(new_plan)
        db.flush()  # Get the plan ID without committing

        # Create progress entries for each recipe
        self._create_progress_entries(user.id, week_number, recipe_ids_from_agent, db)

        # Record suggestions for cooldown tracking
        self.record_recipe_suggestions(
            user_id=user.id,
            week_number=week_number,
            recipe_ids=recipe_ids_from_agent,
            source="plan",
            db=db,
        )

        # Commit everything
        db.commit()
        db.refresh(new_plan)

        # Load the full recipe objects before returning
        return self.load_recipes_for_plan(new_plan, db)

    def adapt_skill_level(self, user: User, week_number: int, db: Session) -> str:
        """Adapt skill level based on user feedback from previous weeks"""
        if week_number == 1:
            return getattr(user, "skill_level")

        # Get feedback from previous weeks
        previous_feedback = (
            db.query(UserRecipeProgress)
            .filter(
                UserRecipeProgress.user_id == user.id,
                UserRecipeProgress.week_number < week_number,
                UserRecipeProgress.feedback.isnot(None),
            )
            .all()
        )

        if not previous_feedback:
            return getattr(user, "skill_level")

        # Analyze feedback patterns
        too_easy_count = sum(
            1 for f in previous_feedback if getattr(f, "feedback") == "too_easy"
        )
        too_hard_count = sum(
            1 for f in previous_feedback if getattr(f, "feedback") == "too_hard"
        )
        total_feedback = len(previous_feedback)

        # Calculate adaptation thresholds
        easy_threshold = 0.6  # 60% too easy
        hard_threshold = 0.4  # 40% too hard

        current_skill = getattr(user, "skill_level")

        # Adapt skill level based on feedback
        if too_easy_count / total_feedback >= easy_threshold:
            if current_skill == "beginner":
                return "intermediate"
            elif current_skill == "intermediate":
                return "advanced"
        elif too_hard_count / total_feedback >= hard_threshold:
            if current_skill == "advanced":
                return "intermediate"
            elif current_skill == "intermediate":
                return "beginner"

        return current_skill

    def unlock_next_week(self, user: User, current_week: int, db: Session) -> bool:
        """Unlock the next week if current week is completed"""
        # Check if all recipes in current week are completed
        current_week_progress = (
            db.query(UserRecipeProgress)
            .filter(
                UserRecipeProgress.user_id == user.id,
                UserRecipeProgress.week_number == current_week,
            )
            .all()
        )

        if not current_week_progress:
            return False

        completed_count = sum(
            1 for p in current_week_progress if getattr(p, "status") == "completed"
        )

        if completed_count == len(current_week_progress):
            # Unlock next week
            next_week = current_week + 1
            next_plan = (
                db.query(WeeklyPlan)
                .filter(
                    WeeklyPlan.user_id == user.id,
                    WeeklyPlan.week_number == next_week,
                )
                .first()
            )
            if next_plan:
                setattr(next_plan, "is_unlocked", True)
                db.commit()
                return True

        return False

    def process_feedback(
        self,
        user_id: int,
        recipe_id: int,
        week_number: int,
        feedback: str,
        db: Session,
        notes: str | None = None,
    ) -> bool:
        """Process user feedback for a recipe"""
        progress = (
            db.query(UserRecipeProgress)
            .filter(
                UserRecipeProgress.user_id == user_id,
                UserRecipeProgress.recipe_id == recipe_id,
                UserRecipeProgress.week_number == week_number,
            )
            .first()
        )

        if not progress:
            return False

        # Update progress
        setattr(progress, "status", "completed")
        setattr(progress, "feedback", feedback)
        if notes is not None:
            setattr(progress, "notes", notes.strip() or None)
        setattr(progress, "completed_at", datetime.now(timezone.utc))

        db.commit()

        # Check if we should unlock next week
        user = db.query(User).filter(User.id == user_id).first()
        if user:
            self.unlock_next_week(user, week_number, db)

        return True

    def get_progress_summary(self, user_id: int, db: Session) -> dict:
        """Get user progress summary"""
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return {}

        # Get all progress entries
        all_progress = (
            db.query(UserRecipeProgress)
            .filter(UserRecipeProgress.user_id == user_id)
            .all()
        )

        completed_progress = [
            p for p in all_progress if getattr(p, "status") == "completed"
        ]

        # Calculate current week
        current_week = self.get_current_week(user, db)

        # Calculate skill progression based on feedback
        recent_feedback = [
            getattr(p, "feedback")
            for p in completed_progress[-10:]
            if getattr(p, "feedback")
        ]
        skill_progression = "stable"

        if recent_feedback:
            easy_count = recent_feedback.count("too_easy")
            hard_count = recent_feedback.count("too_hard")

            if easy_count > hard_count and easy_count > len(recent_feedback) * 0.5:
                skill_progression = "advancing"
            elif hard_count > easy_count and hard_count > len(recent_feedback) * 0.5:
                skill_progression = "needs_support"

        return {
            "total_recipes": len(all_progress),
            "completed_recipes": len(completed_progress),
            "current_week": current_week,
            "completion_rate": (
                len(completed_progress) / len(all_progress) if all_progress else 0
            ),
            "skill_progression": skill_progression,
        }


def validate_recipe_can_be_swapped(
    user_id: uuid.UUID,
    recipe_id: uuid.UUID,
    week_number: int,
    db: Session,
) -> None:
    """
    Validates that a recipe can be swapped (not completed).
    Raises HTTPException(400) if recipe is completed.

    Args:
        user_id: The user's UUID
        recipe_id: The recipe UUID to check
        week_number: The week number
        db: Database session

    Raises:
        HTTPException: If recipe is already completed
    """
    progress = (
        db.query(UserRecipeProgress)
        .filter(
            UserRecipeProgress.user_id == user_id,
            UserRecipeProgress.recipe_id == recipe_id,
            UserRecipeProgress.week_number == week_number,
        )
        .first()
    )

    if progress and progress.status == "completed":
        raise HTTPException(
            status_code=400,
            detail="Cannot swap a completed recipe",
        )


def cleanup_swapped_recipe_progress(
    user_id: uuid.UUID,
    old_recipe_id: uuid.UUID,
    week_number: int,
    db: Session,
) -> None:
    """
    Deletes progress for a swapped-out recipe.
    Prevents orphaned progress entries.

    Args:
        user_id: The user's UUID
        old_recipe_id: The recipe UUID that was removed
        week_number: The week number
        db: Database session
    """
    progress = (
        db.query(UserRecipeProgress)
        .filter(
            UserRecipeProgress.user_id == user_id,
            UserRecipeProgress.recipe_id == old_recipe_id,
            UserRecipeProgress.week_number == week_number,
        )
        .first()
    )

    if progress:
        db.delete(progress)
        print(
            f"[SwapCleanup] Deleted progress for swapped recipe {old_recipe_id} in week {week_number}"
        )
