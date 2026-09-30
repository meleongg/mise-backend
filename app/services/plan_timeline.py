"""Deterministic prep-day timeline and recipe explanations for a verified plan.

Computed on read/generate — not persisted. Safe for client display on an
already-revealed plan (recipe names/IDs are allowed here; they are not
pre-persist candidates).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence
from uuid import UUID

from app.models import Recipe, User

ADVANCE_PREP_MINUTES = 20
TIMELINE_KIND = "deterministic_v1"


@dataclass
class PrepTimelineItem:
    kind: str  # shop | advance_prep | cook
    title: str
    detail: str
    day_label: str
    duration_minutes: Optional[int] = None
    recipe_id: Optional[str] = None
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "title": self.title,
            "detail": self.detail,
            "day_label": self.day_label,
            "duration_minutes": self.duration_minutes,
            "recipe_id": self.recipe_id,
            "reasons": list(self.reasons),
        }


@dataclass
class PrepTimeline:
    items: list[PrepTimelineItem] = field(default_factory=list)
    total_active_minutes: int = 0
    notes: list[str] = field(default_factory=list)
    kind: str = TIMELINE_KIND

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "total_active_minutes": self.total_active_minutes,
            "notes": list(self.notes),
            "items": [item.as_dict() for item in self.items],
        }


def _parse_json_list(raw: Optional[str]) -> list[str]:
    if not raw or not str(raw).strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [str(x).strip().lower() for x in data if str(x).strip()]


def _skill_rank(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    key = str(value).strip().lower()
    mapping = {
        "beginner": 1,
        "easy": 1,
        "intermediate": 2,
        "medium": 2,
        "advanced": 3,
        "hard": 3,
    }
    return mapping.get(key)


def recipe_explanations(user: User, recipe: Recipe) -> list[str]:
    """Preference-based reasons a verified recipe fits this user."""
    reasons: list[str] = []
    preferred = (getattr(user, "cuisine", None) or "").strip().lower()
    recipe_cuisine = (getattr(recipe, "cuisine", None) or "").strip().lower()
    if preferred and recipe_cuisine and preferred == recipe_cuisine:
        reasons.append(f"Matches your {recipe.cuisine} preference.")

    max_prep = getattr(user, "max_prep_time_minutes", None)
    max_cook = getattr(user, "max_cook_time_minutes", None)
    prep = getattr(recipe, "prep_time_minutes", None)
    cook = getattr(recipe, "cook_time_minutes", None)
    if max_prep is not None and prep is not None and int(prep) <= int(max_prep):
        reasons.append(f"Prep stays within your {int(max_prep)} min cap.")
    if max_cook is not None and cook is not None and int(cook) <= int(max_cook):
        reasons.append(f"Cook stays within your {int(max_cook)} min cap.")

    user_skill = _skill_rank(getattr(user, "skill_level", None))
    recipe_skill = _skill_rank(
        getattr(recipe, "skill_level_validated", None)
        or getattr(recipe, "difficulty", None)
    )
    if user_skill is not None and recipe_skill is not None:
        if recipe_skill <= user_skill:
            reasons.append("Difficulty fits your current skill level.")
        elif recipe_skill == user_skill + 1:
            reasons.append("Slight stretch — good for leveling up.")

    diet = set(_parse_json_list(getattr(user, "dietary_restrictions", None)))
    tags = set(_parse_json_list(getattr(recipe, "dietary_tags", None)))
    if diet and tags and diet.issubset(tags):
        reasons.append("Honors your dietary tags.")

    if not reasons:
        reasons.append("Selected for this week's verified plan.")
    return reasons[:3]


def build_prep_timeline(
    user: User,
    recipes: Sequence[Recipe],
    *,
    ordered_recipe_ids: Optional[Sequence[UUID]] = None,
) -> PrepTimeline:
    """Build shop → advance prep → cook-day windows from recipe times."""
    by_id = {r.id: r for r in recipes}
    ordered: list[Recipe] = []
    if ordered_recipe_ids:
        for rid in ordered_recipe_ids:
            recipe = by_id.get(rid)
            if recipe is not None:
                ordered.append(recipe)
        for recipe in recipes:
            if recipe not in ordered:
                ordered.append(recipe)
    else:
        ordered = list(recipes)

    items: list[PrepTimelineItem] = []
    notes: list[str] = []
    total_active = 0
    missing_time = 0

    if ordered:
        retailer = (getattr(user, "preferred_retailer", None) or "").strip()
        city = (getattr(user, "city", None) or "").strip()
        shop_bits = ["Pull a best-effort list from this week's plan."]
        if retailer:
            shop_bits.append(f"Aim for {retailer}.")
        if city:
            shop_bits.append(f"Location: {city}.")
        items.append(
            PrepTimelineItem(
                kind="shop",
                title="Shop for the week",
                detail=" ".join(shop_bits),
                day_label="Before the week",
                duration_minutes=None,
                reasons=["One shopping pass covers all planned recipes."],
            )
        )

    for recipe in ordered:
        prep = getattr(recipe, "prep_time_minutes", None)
        cook = getattr(recipe, "cook_time_minutes", None)
        if prep is None and cook is None:
            missing_time += 1
        else:
            total_active += int(prep or 0) + int(cook or 0)

        if prep is not None and int(prep) >= ADVANCE_PREP_MINUTES:
            items.append(
                PrepTimelineItem(
                    kind="advance_prep",
                    title=f"Advance prep: {recipe.name}",
                    detail=(
                        f"About {int(prep)} min of prep can happen ahead "
                        "so cook day is lighter."
                    ),
                    day_label="Advance prep",
                    duration_minutes=int(prep),
                    recipe_id=str(recipe.id),
                    reasons=["Prep time is long enough to split ahead."],
                )
            )

    for index, recipe in enumerate(ordered, start=1):
        prep = getattr(recipe, "prep_time_minutes", None)
        cook = getattr(recipe, "cook_time_minutes", None)
        if prep is not None and int(prep) >= ADVANCE_PREP_MINUTES:
            # Prep already scheduled as advance_prep; cook window is cook-only.
            duration = int(cook) if cook is not None else None
            detail_bits = []
            if cook is not None:
                detail_bits.append(f"~{int(cook)} min cook after advance prep.")
            else:
                detail_bits.append("Cook day — prep already scheduled earlier.")
        else:
            duration = None
            if prep is not None or cook is not None:
                duration = int(prep or 0) + int(cook or 0)
            detail_bits = []
            if prep is not None:
                detail_bits.append(f"{int(prep)} min prep")
            if cook is not None:
                detail_bits.append(f"{int(cook)} min cook")
            if not detail_bits:
                detail_bits.append("Timing not listed — review before cooking.")

        items.append(
            PrepTimelineItem(
                kind="cook",
                title=recipe.name,
                detail=" · ".join(detail_bits),
                day_label=f"Day {index}",
                duration_minutes=duration,
                recipe_id=str(recipe.id),
                reasons=recipe_explanations(user, recipe),
            )
        )

    if missing_time:
        notes.append(
            f"{missing_time} recipe(s) missing prep/cook times — "
            "timeline durations are incomplete."
        )

    max_prep = getattr(user, "max_prep_time_minutes", None)
    max_cook = getattr(user, "max_cook_time_minutes", None)
    frequency = int(getattr(user, "frequency", 0) or 0)
    if max_prep is not None and max_cook is not None and frequency > 0:
        budget = frequency * (int(max_prep) + int(max_cook))
        if budget > 0 and total_active > budget:
            notes.append(
                "Total active minutes exceed your weekly prep+cook budget."
            )

    return PrepTimeline(
        items=items,
        total_active_minutes=total_active,
        notes=notes,
    )
