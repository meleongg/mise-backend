"""
Build searchable text for recipe embeddings (vector similarity).
"""

from __future__ import annotations

import json
from typing import Any, List, Optional, Union

from app.models import Recipe
from app.utils.recipe_formatters import instructions_json_to_text


def _parse_json_string_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, list):
                return [str(v).strip() for v in parsed if str(v).strip()]
        except json.JSONDecodeError:
            return [stripped]
    return []


def build_recipe_content_text(
    *,
    name: str,
    cuisine: str,
    ingredients_text: str = "",
    instructions_text: str = "",
    dietary_tags: Optional[List[str]] = None,
    allergens: Optional[List[str]] = None,
    portion_size: Optional[str] = None,
    prep_time_minutes: Optional[int] = None,
    cook_time_minutes: Optional[int] = None,
    skill_level_validated: Optional[str] = None,
    difficulty: Optional[str] = None,
) -> str:
    """Single blob embedded for vector search; includes all plan-relevant metadata."""
    parts: List[str] = [name, cuisine]
    if difficulty:
        parts.append(f"difficulty {difficulty}")
    if skill_level_validated:
        parts.append(f"skill level {skill_level_validated}")
    if dietary_tags:
        parts.append("dietary " + " ".join(dietary_tags))
    if allergens:
        parts.append("contains allergens " + " ".join(allergens))
    if portion_size:
        parts.append(f"portion {portion_size}")
    if prep_time_minutes is not None:
        parts.append(f"prep {prep_time_minutes} minutes")
    if cook_time_minutes is not None:
        parts.append(f"cook {cook_time_minutes} minutes")
    if ingredients_text:
        parts.append(ingredients_text)
    if instructions_text:
        parts.append(instructions_text)
    return " ".join(parts).strip()


def content_text_from_recipe(recipe: Recipe) -> str:
    """Rebuild embedding text from a persisted Recipe row."""
    ingredients_raw = recipe.ingredients or "[]"
    try:
        ingredients = json.loads(ingredients_raw)
        ingredients_text = " ".join(
            f"{item.get('name', '')} {item.get('measure', '')}".strip()
            for item in ingredients
            if isinstance(item, dict)
        )
    except json.JSONDecodeError:
        ingredients_text = ingredients_raw

    instructions_raw = recipe.instructions or "[]"
    try:
        instructions = json.loads(instructions_raw)
        instructions_text = (
            instructions_json_to_text(instructions)
            if isinstance(instructions, list)
            else instructions_raw
        )
    except json.JSONDecodeError:
        instructions_text = instructions_raw

    return build_recipe_content_text(
        name=recipe.name or "",
        cuisine=recipe.cuisine or "",
        ingredients_text=ingredients_text,
        instructions_text=instructions_text,
        dietary_tags=_parse_json_string_list(recipe.dietary_tags),
        allergens=_parse_json_string_list(recipe.allergens),
        portion_size=recipe.portion_size,
        prep_time_minutes=recipe.prep_time_minutes,
        cook_time_minutes=recipe.cook_time_minutes,
        skill_level_validated=recipe.skill_level_validated,
        difficulty=recipe.difficulty,
    )
