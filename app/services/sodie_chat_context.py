"""
Build compact context strings for Sodie coach chat (adaptive_chat / general).
"""

from __future__ import annotations

import json
import uuid
from typing import Dict, List, Optional, Tuple

from fastapi import HTTPException
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


def _truncate(text: str, limit: int = 1200) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _format_ingredients(raw: Optional[str]) -> str:
    if not raw:
        return "none listed"
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return _truncate(str(raw), 800)
    if isinstance(parsed, list):
        lines = []
        for item in parsed[:40]:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                measure = str(item.get("measure") or "").strip()
                if name:
                    lines.append(f"{measure} {name}".strip() if measure else name)
            elif item:
                lines.append(str(item))
        return _truncate("; ".join(lines) if lines else "none listed", 800)
    return _truncate(str(parsed), 800)


def _format_instructions(raw: Optional[str]) -> str:
    if not raw:
        return "none listed"
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return _truncate(str(raw), 1200)
    if isinstance(parsed, list):
        lines = []
        for idx, item in enumerate(parsed[:20], start=1):
            if isinstance(item, dict):
                text = str(item.get("text") or "").strip()
                if text:
                    lines.append(f"{idx}. {text}")
            elif item:
                lines.append(f"{idx}. {item}")
        return _truncate(" ".join(lines) if lines else "none listed", 1200)
    return _truncate(str(parsed), 1200)


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


def _parse_uuid(value: Optional[str]) -> Optional[uuid.UUID]:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def authorize_page_context(
    db: Session, user: User, scope: str, context_id: Optional[str]
) -> Tuple[str, Optional[int]]:
    """
    Validate scope/context_id and return (page_snapshot_text, plan_week_for_profile).

    Never trust client-supplied recipe/plan content — only load from DB.
    """
    scope = (scope or "global").strip().lower()
    if scope not in {"global", "plan", "recipe", "kitchen", "shopping"}:
        raise HTTPException(status_code=422, detail=f"Unsupported Sodie scope: {scope}")

    if scope == "global":
        return (
            "ACTIVE PAGE: global\n"
            "- No page-specific recipe or plan entity is selected.\n",
            None,
        )

    if scope == "shopping":
        return (
            "ACTIVE PAGE: shopping\n"
            "- Shopping mode context is not available yet; answer with general "
            "prep/shopping advice without inventing list items.\n",
            None,
        )

    if scope == "plan":
        plan: Optional[WeeklyPlan] = None
        parsed = _parse_uuid(context_id)
        if parsed:
            plan = (
                db.query(WeeklyPlan)
                .filter(WeeklyPlan.id == parsed, WeeklyPlan.user_id == user.id)
                .first()
            )
            if not plan:
                raise HTTPException(status_code=404, detail="Weekly plan not found")
        elif context_id and str(context_id).isdigit():
            plan = _resolve_weekly_plan(db, user.id, int(context_id))
            if not plan:
                raise HTTPException(status_code=404, detail="Weekly plan not found")
        else:
            plan = _resolve_weekly_plan(db, user.id, None)

        if not plan:
            return (
                "ACTIVE PAGE: plan\n"
                "- ACTIVE_PLAN: none\n"
                "- Encourage generating a weekly plan before week-specific advice.\n",
                None,
            )

        plan_service = WeeklyPlanService()
        plan_service.load_recipes_for_plan(plan, db)
        recipes: List[Recipe] = getattr(plan, "recipes", []) or []
        recipe_ids = parse_recipe_schedule(plan.recipe_schedule)
        recipes_dict = {str(r.id): r for r in recipes}
        ordered = [recipes_dict[rid] for rid in recipe_ids if rid in recipes_dict]
        meal_lines = []
        for idx, recipe in enumerate(ordered, start=1):
            meal_lines.append(
                f"  {idx}. {recipe.name} (id={recipe.id}; cuisine={recipe.cuisine}; "
                f"difficulty={recipe.difficulty})"
            )
        body = "\n".join(
            [
                "ACTIVE PAGE: plan",
                f"- Weekly plan id: {plan.id}",
                f"- Week number: {plan.week_number}",
                f"- Swaps used: {getattr(plan, 'swap_count', 0) or 0}",
                "- Meals on this plan page:",
                *(meal_lines or ["  (no recipes scheduled)"]),
            ]
        )
        return body + "\n", plan.week_number

    recipe_id = _parse_uuid(context_id)
    if not recipe_id:
        return (
            f"ACTIVE PAGE: {scope}\n"
            "- No recipe context_id provided; answer without inventing dish details.\n",
            None,
        )
    recipe = db.query(Recipe).filter(Recipe.id == recipe_id).first()
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")

    mode = "kitchen" if scope == "kitchen" else "recipe"
    lines = [
        f"ACTIVE PAGE: {mode}",
        f"- Recipe id: {recipe.id}",
        f"- Title: {recipe.name}",
        f"- Cuisine: {recipe.cuisine}",
        f"- Difficulty: {recipe.difficulty}",
        f"- Servings: {getattr(recipe, 'portion_size', None) or 'not set'}",
        f"- Dietary tags: {_format_json_field(getattr(recipe, 'dietary_tags', None))}",
        f"- Allergens in dish: {_format_json_field(getattr(recipe, 'allergens', None))}",
        f"- Ingredients: {_format_ingredients(recipe.ingredients)}",
        f"- Instructions: {_format_instructions(recipe.instructions)}",
    ]
    if mode == "kitchen":
        lines.append(
            "- User is in Kitchen Mode (step-by-step cooking). Prefer concise "
            "timing/technique help for this recipe."
        )
    else:
        lines.append(
            "- User is viewing this catalog recipe page. Prefer prep, technique, "
            "and edit guidance for this dish (Swap is a separate plan-slot control)."
        )
    return "\n".join(lines) + "\n", None


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


def build_sodie_prompt_context(
    db: Session,
    user: User,
    *,
    scope: str = "global",
    context_id: Optional[str] = None,
) -> str:
    """Profile/plan context plus authorized page snapshot for coach prompts."""
    page_block, week_number = authorize_page_context(db, user, scope, context_id)
    profile = build_sodie_chat_context(db, user, week_number=week_number)
    return f"{profile}\n\n{page_block}"
