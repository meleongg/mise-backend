"""
Build compact context strings for Sodie coach chat.
"""

from __future__ import annotations

import json
import uuid
from typing import Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.constants import MAX_SWAPS_PER_WEEK
from app.models import PersonalRecipe, Recipe, User, UserRecipeProgress, WeeklyPlan
from app.services.weekly_plan import WeeklyPlanService, parse_recipe_schedule
from app.services import shopping as shopping_service
from app.utils.prompt_helpers import get_goal_description, get_skill_description

SUPPORTED_SODIE_SCOPES = frozenset(
    {
        "global",
        "plan",
        "recipe",
        "kitchen",
        "shopping",
        "personal_recipe",
        "settings",
        "analytics",
    }
)


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


def _analytics_page_snapshot(db: Session, user: User) -> str:
    """Trusted progress aggregates for /analytics — never invent stats."""
    plan_service = WeeklyPlanService()
    summary = plan_service.get_progress_summary(user.id, db)
    plans = (
        db.query(WeeklyPlan)
        .filter(WeeklyPlan.user_id == user.id)
        .order_by(WeeklyPlan.week_number.desc())
        .all()
    )
    progress_rows = (
        db.query(UserRecipeProgress)
        .filter(UserRecipeProgress.user_id == user.id)
        .all()
    )
    feedback_counts = {"too_easy": 0, "just_right": 0, "too_hard": 0}
    for row in progress_rows:
        fb = getattr(row, "feedback", None)
        if fb in feedback_counts:
            feedback_counts[fb] += 1

    completed = [
        p for p in progress_rows if getattr(p, "status", None) == "completed"
    ]
    # Lightweight cuisine counts from completed recipes (catalog join).
    cuisine_counts: Dict[str, int] = {}
    recipe_ids = [p.recipe_id for p in completed if getattr(p, "recipe_id", None)]
    if recipe_ids:
        recipes = db.query(Recipe).filter(Recipe.id.in_(recipe_ids)).all()
        by_id = {r.id: r for r in recipes}
        for pid in recipe_ids:
            recipe = by_id.get(pid)
            if not recipe:
                continue
            cuisine = getattr(recipe, "cuisine", None) or "unknown"
            cuisine_counts[cuisine] = cuisine_counts.get(cuisine, 0) + 1
    top_cuisines = sorted(cuisine_counts.items(), key=lambda x: (-x[1], x[0]))[:5]
    cuisine_line = (
        ", ".join(f"{name}×{count}" for name, count in top_cuisines)
        if top_cuisines
        else "none yet"
    )

    total_recipes = int(summary.get("total_recipes") or 0)
    completed_recipes = int(summary.get("completed_recipes") or 0)
    rate = float(summary.get("completion_rate") or 0.0)
    lines = [
        "ACTIVE PAGE: analytics",
        "- User is viewing their Cooking Journey analytics.",
        f"- Total weekly plans: {len(plans)}",
        f"- Current week number: {summary.get('current_week') or 'none'}",
        f"- Recipes tracked: {total_recipes}",
        f"- Recipes completed: {completed_recipes}",
        f"- Completion rate: {rate:.0%} ({completed_recipes}/{total_recipes or 0})",
        f"- Skill progression signal: {summary.get('skill_progression') or 'unknown'}",
        (
            "- Feedback counts (completed with feedback): "
            f"too_easy={feedback_counts['too_easy']}, "
            f"just_right={feedback_counts['just_right']}, "
            f"too_hard={feedback_counts['too_hard']}"
        ),
        f"- Top cuisines among completed recipes: {cuisine_line}",
        "- Answer using ONLY these aggregates and the USER PROFILE / ACTIVE_PLAN "
        "blocks when present. Do not invent weeks, rates, or recipes. Prefer "
        "plain-language pattern insights (streaks/fit/trends). Recipe or "
        "preference change suggestions must stay conversational — no silent writes.",
    ]
    return "\n".join(lines) + "\n"


def authorize_page_context(
    db: Session, user: User, scope: str, context_id: Optional[str]
) -> Tuple[str, Optional[int]]:
    """
    Validate scope/context_id and return (page_snapshot_text, plan_week_for_profile).

    Never trust client-supplied recipe/plan content — only load from DB.
    """
    scope = (scope or "global").strip().lower()
    if scope not in SUPPORTED_SODIE_SCOPES:
        raise HTTPException(status_code=422, detail=f"Unsupported Sodie scope: {scope}")

    if scope == "global":
        return (
            "ACTIVE PAGE: global\n"
            "- No page-specific recipe or plan entity is selected.\n",
            None,
        )

    if scope == "settings":
        # Privacy: no cooking profile / plan dump — UI help only.
        return (
            "ACTIVE PAGE: settings\n"
            "- User is on Preferences or Account settings.\n"
            "- Answer only about Mise settings UI (preferences, account, "
            "privacy toggles). Do not invent pantry/plan/recipe details and "
            "do not assume cooking goals from memory.\n",
            None,
        )

    if scope == "analytics":
        return _analytics_page_snapshot(db, user), None

    if scope == "shopping":
        active = shopping_service.get_active_shopping_list(db, user)
        if not active:
            return (
                "ACTIVE PAGE: shopping\n"
                "- No active shopping list yet. Suggest generating from the "
                "weekly plan; do not invent list items.\n",
                None,
            )
        checked = sum(1 for item in active.items if item.is_checked)
        total = len(active.items or [])
        preview = []
        for item in (active.items or [])[:12]:
            mark = "✓" if item.is_checked else "○"
            review = " (review)" if item.needs_review else ""
            preview.append(f"  {mark} {item.display_text}{review}")
        more = ""
        if total > 12:
            more = f"\n- …and {total - 12} more items"
        return (
            "ACTIVE PAGE: shopping\n"
            f"- List: {active.title} ({active.status})\n"
            f"- Retailer snapshot: {active.retailer_snapshot or 'not set'}\n"
            f"- Location snapshot: {active.location_snapshot or 'not set'}\n"
            f"- Progress: {checked}/{total} checked\n"
            "- Items (do not invent extras):\n"
            + ("\n".join(preview) if preview else "  (empty)")
            + more
            + "\n",
            str(active.id),
        )

    if scope == "personal_recipe":
        personal_id = _parse_uuid(context_id)
        if not personal_id:
            return (
                "ACTIVE PAGE: personal_recipe\n"
                "- User is browsing My Recipes (no specific personal copy selected).\n"
                "- Help them find or talk about their edited recipes; do not "
                "invent personal-recipe content.\n",
                None,
            )
        personal = (
            db.query(PersonalRecipe)
            .filter(
                PersonalRecipe.id == personal_id,
                PersonalRecipe.user_id == user.id,
                PersonalRecipe.is_active.is_(True),
            )
            .first()
        )
        if not personal:
            raise HTTPException(status_code=404, detail="Personal recipe not found")
        lines = [
            "ACTIVE PAGE: personal_recipe",
            f"- Personal recipe id: {personal.id}",
            f"- Title: {personal.name}",
            f"- Revision: {personal.current_revision}",
            f"- Servings: {personal.portion_size or 'not set'}",
            f"- Source catalog recipe id: {personal.source_recipe_id or 'none'}",
            f"- Ingredients: {_format_ingredients(personal.ingredients)}",
            f"- Instructions: {_format_instructions(personal.instructions)}",
            f"- Notes: {_truncate(personal.notes or 'none', 400)}",
            "- This is the user’s owned My Recipes copy (not the shared catalog). "
            "Prefer technique help for this personal version. For content edits, "
            "tell them to use Edit with Sodie — coach chat cannot submit proposals.",
        ]
        return "\n".join(lines) + "\n", None

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
            "timing/technique help for this recipe. For ingredient/step changes, "
            "tell them to use Edit with Sodie — coach chat cannot submit proposals."
        )
    else:
        lines.append(
            "- User is viewing this catalog recipe page. Prefer prep and technique "
            "help. If they ask to change ingredients, steps, servings, or notes, "
            "tell them to tap Edit with Sodie for a reviewable before/after "
            "proposal — never invent a verbal proposal or ask to “submit” one. "
            "Swap is a separate plan-slot control."
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
    # Settings must not inject durable cooking/plan/profile snapshots.
    if (scope or "").strip().lower() == "settings":
        return page_block
    profile = build_sodie_chat_context(db, user, week_number=week_number)
    return f"{profile}\n\n{page_block}"
