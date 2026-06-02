"""
Build compact context strings for Sodie coach chat (adaptive_chat / general).
"""

import json
import uuid
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.constants import MAX_SWAPS_PER_WEEK
from app.models import Recipe, User, UserRecipeProgress, WeeklyPlan
from app.services.weekly_plan import WeeklyPlanService, parse_recipe_schedule
from app.utils.prompt_helpers import get_goal_description, get_skill_description


def _parse_json_list(value: Optional[str]) -> List[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    except (json.JSONDecodeError, TypeError):
        pass
    return []


def _format_json_field(value: Optional[str], fallback: str = "none") -> str:
    items = _parse_json_list(value)
    return ", ".join(items) if items else fallback


def _resolve_weekly_plan(
    db: Session, user_id: uuid.UUID, week_number: Optional[int]
) -> Optional[WeeklyPlan]:
    if week_number is not None:
        return (
            db.query(WeeklyPlan)
            .filter(
                WeeklyPlan.user_id == user_id,
                WeeklyPlan.week_number == week_number,
            )
            .first()
        )
    return (
        db.query(WeeklyPlan)
        .filter(WeeklyPlan.user_id == user_id)
        .order_by(WeeklyPlan.week_number.desc())
        .first()
    )


def build_sodie_chat_context(
    db: Session, user: User, week_number: Optional[int] = None
) -> str:
    """Build a compact text block for LLM prompts from profile, plan, and progress."""
    dietary = _format_json_field(getattr(user, "dietary_restrictions", None))
    allergens = _format_json_field(getattr(user, "allergens", None))
    prep_cap = getattr(user, "max_prep_time_minutes", None)
    cook_cap = getattr(user, "max_cook_time_minutes", None)
    portion = getattr(user, "preferred_portion_size", None) or "not specified"

    lines = [
        "USER PROFILE:",
        f"- Name: {user.first_name}",
        f"- Goal: {get_goal_description(user.user_goal)}",
        f"- Skill: {get_skill_description(user.skill_level)}",
        f"- Preferred cuisine: {user.cuisine}",
        f"- Meals per week: {user.frequency}",
        f"- Dietary restrictions: {dietary}",
        f"- Allergens to avoid: {allergens}",
        f"- Max prep time (minutes): {prep_cap if prep_cap is not None else 'not set'}",
        f"- Max cook time (minutes): {cook_cap if cook_cap is not None else 'not set'}",
        f"- Preferred portion size: {portion}",
    ]

    plan = _resolve_weekly_plan(db, user.id, week_number)
    if not plan:
        lines.extend(
            [
                "",
                "ACTIVE_PLAN: none",
                "The user has not generated a weekly meal plan yet.",
            ]
        )
        plan_service = WeeklyPlanService()
        summary = plan_service.get_progress_summary(user.id, db)
        if summary.get("skill_progression"):
            lines.append(
                f"- Overall skill trend (all time): {summary.get('skill_progression')}"
            )
        return "\n".join(lines)

    plan_service = WeeklyPlanService()
    plan_service.load_recipes_for_plan(plan, db)
    recipes: List[Recipe] = getattr(plan, "recipes", []) or []

    week_progress = (
        db.query(UserRecipeProgress)
        .filter(
            UserRecipeProgress.user_id == user.id,
            UserRecipeProgress.week_number == plan.week_number,
        )
        .all()
    )
    status_by_recipe: Dict[str, str] = {
        str(p.recipe_id): getattr(p, "status", "not_started") for p in week_progress
    }

    completed_count = sum(1 for s in status_by_recipe.values() if s == "completed")
    total_count = len(recipes) if recipes else len(status_by_recipe)
    swap_count = getattr(plan, "swap_count", 0) or 0
    swaps_remaining = max(0, MAX_SWAPS_PER_WEEK - swap_count)

    lines.extend(
        [
            "",
            f"ACTIVE_PLAN: week {plan.week_number}",
            f"- Week progress: {completed_count}/{total_count} recipes completed",
            f"- Swaps remaining this week: {swaps_remaining} of {MAX_SWAPS_PER_WEEK}",
            (
                "- Next week: eligible to generate"
                if total_count > 0 and completed_count == total_count
                else "- Next week: complete all recipes in this week first"
            ),
            "- Meals (in plan order):",
        ]
    )

    recipe_ids = parse_recipe_schedule(plan.recipe_schedule)
    recipes_dict = {str(r.id): r for r in recipes}
    ordered_recipes = [recipes_dict[rid] for rid in recipe_ids if rid in recipes_dict]

    for idx, recipe in enumerate(ordered_recipes, start=1):
        rid = str(recipe.id)
        status = status_by_recipe.get(rid, "not_started")
        dietary_tags = _format_json_field(getattr(recipe, "dietary_tags", None))
        recipe_allergens = _format_json_field(getattr(recipe, "allergens", None))
        difficulty = getattr(recipe, "difficulty", "unknown")
        lines.append(
            f"  {idx}. {recipe.name} [{status}] "
            f"(difficulty: {difficulty}; dietary: {dietary_tags}; allergens in dish: {recipe_allergens})"
        )

    summary = plan_service.get_progress_summary(user.id, db)
    if summary.get("skill_progression"):
        lines.append(
            f"- Overall skill trend (all time): {summary['skill_progression']}"
        )

    return "\n".join(lines)
