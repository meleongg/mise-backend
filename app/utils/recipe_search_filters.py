"""
SQL fragments and query enrichment for hybrid recipe search.
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional, Tuple


def normalize_preference_tags(tags: Optional[List[str]]) -> List[str]:
    if not tags:
        return []
    return sorted({str(t).strip().lower() for t in tags if str(t).strip()})


def enrich_intent_query(
    intent_query: str,
    *,
    cuisine: Optional[str] = None,
    dietary_restrictions: Optional[List[str]] = None,
    allergens: Optional[List[str]] = None,
    skill_level: Optional[str] = None,
    portion_size: Optional[str] = None,
    max_prep_time: Optional[int] = None,
    max_cook_time: Optional[int] = None,
) -> str:
    """Bias the query embedding toward user constraints."""
    parts = [intent_query.strip()]
    if cuisine:
        parts.append(f"cuisine {cuisine}")
    dietary = normalize_preference_tags(dietary_restrictions)
    if dietary:
        parts.append("dietary " + " ".join(dietary))
    avoid = normalize_preference_tags(allergens)
    if avoid:
        parts.append("avoid allergens " + " ".join(avoid))
    if skill_level:
        parts.append(f"skill {skill_level}")
    if portion_size:
        parts.append(f"portion {portion_size}")
    if max_prep_time:
        parts.append(f"prep under {max_prep_time} minutes")
    if max_cook_time:
        parts.append(f"cook under {max_cook_time} minutes")
    return " ".join(p for p in parts if p)


def allowed_difficulties_for_skill(skill_level: str) -> List[str]:
    """User skill → recipe.difficulty values (includes legacy seed values)."""
    skill_map = {
        "beginner": ["easy", "beginner"],
        "intermediate": ["easy", "medium", "beginner", "medium"],
        "advanced": ["easy", "medium", "hard", "beginner", "medium", "advanced"],
    }
    return skill_map.get(
        skill_level, ["easy", "medium", "hard", "beginner", "medium", "advanced"]
    )


def allowed_validated_skills_for_skill(skill_level: str) -> List[str]:
    skill_map = {
        "beginner": ["beginner"],
        "intermediate": ["beginner", "medium"],
        "advanced": ["beginner", "medium", "advanced"],
    }
    return skill_map.get(skill_level, ["beginner", "medium", "advanced"])


def dietary_filter_clause(
    dietary_restrictions: Optional[List[str]],
) -> Tuple[str, Dict[str, str]]:
    """
    Recipe dietary_tags (JSON array) must contain every user restriction.
    Recipes without dietary_tags are excluded when user has restrictions.
    """
    normalized = normalize_preference_tags(dietary_restrictions)
    if not normalized:
        return "", {}
    return (
        "r.dietary_tags IS NOT NULL AND TRIM(r.dietary_tags) <> '' "
        "AND r.dietary_tags::jsonb @> :dietary_required::jsonb",
        {"dietary_required": json.dumps(normalized)},
    )


def allergen_filter_clause(
    avoid_allergens: Optional[List[str]],
) -> Tuple[str, Dict[str, str]]:
    """
    Exclude recipes whose allergen list overlaps user allergens to avoid.
    Recipes without allergen metadata are excluded when user has allergens.
    """
    normalized = normalize_preference_tags(avoid_allergens)
    if not normalized:
        return "", {}
    return (
        "r.allergens IS NOT NULL AND TRIM(r.allergens) <> '' "
        "AND NOT (r.allergens::jsonb && :avoid_allergens::jsonb)",
        {"avoid_allergens": json.dumps(normalized)},
    )
