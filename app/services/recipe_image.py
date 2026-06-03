"""
Resolve recipe hero images via the Pexels API.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, List, Optional
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from app.models import Recipe
from app.utils.recipe_search_filters import normalize_preference_tags

logger = logging.getLogger(__name__)

PEXELS_SEARCH_URL = "https://api.pexels.com/v1/search"
PEXELS_IMAGE_HOST = "images.pexels.com"
DEFAULT_PER_PAGE = 5
REQUEST_TIMEOUT_SEC = 8.0

# Strip marketing/skill prefixes from legacy AI names when searching Pexels.
_MARKETING_PREFIX_RE = re.compile(
    r"^((?:beginner|beginners|easy|simple|classic|ultimate|perfect|homestyle|"
    r"mastery|quick|healthy|delicious|amazing)\s*['']?s?\s+)+",
    re.IGNORECASE,
)

# Alt-text tokens that clash with common dietary tags (stock photos are often mislabeled).
_DIETARY_ALT_CONFLICTS: dict[str, frozenset[str]] = {
    "vegetarian": frozenset(
        {
            "bacon",
            "beef",
            "chicken",
            "crab",
            "egg",
            "eggs",
            "fish",
            "ham",
            "lamb",
            "lobster",
            "meat",
            "pork",
            "salmon",
            "sausage",
            "seafood",
            "shrimp",
            "steak",
            "tuna",
            "turkey",
        }
    ),
    "vegan": frozenset(
        {
            "bacon",
            "beef",
            "butter",
            "cheese",
            "chicken",
            "cream",
            "crab",
            "dairy",
            "egg",
            "eggs",
            "fish",
            "ham",
            "honey",
            "lamb",
            "lobster",
            "meat",
            "milk",
            "pork",
            "salmon",
            "sausage",
            "seafood",
            "shrimp",
            "steak",
            "tuna",
            "turkey",
            "yogurt",
        }
    ),
    "pescatarian": frozenset(
        {
            "bacon",
            "beef",
            "chicken",
            "ham",
            "lamb",
            "meat",
            "pork",
            "sausage",
            "steak",
            "turkey",
        }
    ),
}

# Strongest diet label to bias Pexels search (one primary term keeps queries short).
_DIETARY_SEARCH_PRIORITY = (
    "vegan",
    "vegetarian",
    "pescatarian",
    "gluten-free",
    "dairy-free",
)

_PANTRY_INGREDIENT_WORDS = frozenset(
    {
        "salt",
        "pepper",
        "oil",
        "water",
        "sugar",
        "flour",
        "butter",
        "garlic",
        "onion",
        "soy",
        "sauce",
        "stock",
        "broth",
        "vinegar",
        "spice",
        "seasoning",
    }
)


def pexels_enabled() -> bool:
    if os.getenv("PEXELS_API_ENABLED", "true").lower() in ("0", "false", "no"):
        return False
    return bool(os.getenv("PEXELS_API_KEY", "").strip())


def _tokenize(text: str) -> set[str]:
    return {t.lower() for t in re.findall(r"[a-zA-Z0-9]+", text) if len(t) > 2}


def searchable_recipe_name(name: str) -> str:
    """Normalize verbose AI titles for stock-photo search."""
    cleaned = (name or "").strip()
    if not cleaned:
        return ""
    prev = None
    while cleaned != prev:
        prev = cleaned
        cleaned = _MARKETING_PREFIX_RE.sub("", cleaned).strip()
    return cleaned or (name or "").strip()


def _primary_ingredient_keyword(raw_name: str) -> Optional[str]:
    """Best search keyword from an ingredient line (e.g. 'rice noodles' -> noodles)."""
    words = [w.lower() for w in re.findall(r"[a-zA-Z]+", raw_name) if len(w) > 2]
    candidates = [w for w in words if w not in _PANTRY_INGREDIENT_WORDS]
    if not candidates:
        return None
    return max(candidates, key=len)


def main_ingredient_keywords(ingredients_json: str, *, max_count: int = 2) -> List[str]:
    """Up to N distinctive ingredient words for Pexels queries."""
    try:
        data = json.loads(ingredients_json)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []

    keywords: List[str] = []
    seen: set[str] = set()

    for item in data:
        if len(keywords) >= max_count:
            break
        name = ""
        if isinstance(item, dict):
            name = str(item.get("name") or "")
        elif isinstance(item, str):
            name = item
        token = _primary_ingredient_keyword(name)
        if not token or token in seen:
            continue
        seen.add(token)
        keywords.append(token)

    return keywords


def _parse_recipe_dietary_tags(recipe: Recipe) -> List[str]:
    raw = getattr(recipe, "dietary_tags", None)
    if not raw or not str(raw).strip():
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return normalize_preference_tags([str(t) for t in parsed])


def merge_dietary_context(
    recipe: Recipe, user_dietary_restrictions: Optional[List[str]] = None
) -> List[str]:
    """Recipe tags plus user profile restrictions (union, normalized)."""
    combined = _parse_recipe_dietary_tags(recipe) + normalize_preference_tags(
        user_dietary_restrictions
    )
    return normalize_preference_tags(combined)


def primary_dietary_search_term(dietary_tags: List[str]) -> Optional[str]:
    """Single stock-photo keyword (e.g. vegetarian) for Pexels queries."""
    tag_set = set(dietary_tags)
    for term in _DIETARY_SEARCH_PRIORITY:
        if term in tag_set:
            return term
    return dietary_tags[0] if dietary_tags else None


def _alt_conflicts_dietary(alt: str, dietary_tags: List[str]) -> bool:
    if not dietary_tags or not alt:
        return False
    alt_tokens = _tokenize(alt)
    for tag in dietary_tags:
        conflicts = _DIETARY_ALT_CONFLICTS.get(tag)
        if conflicts and alt_tokens & conflicts:
            return True
    return False


def build_search_queries(
    recipe: Recipe, *, dietary_tags: Optional[List[str]] = None
) -> List[str]:
    raw_name = (recipe.name or "").strip()
    search_name = searchable_recipe_name(raw_name)
    cuisine = (recipe.cuisine or "").strip()
    ingredients = main_ingredient_keywords(recipe.ingredients or "[]", max_count=2)
    diet = (
        dietary_tags if dietary_tags is not None else _parse_recipe_dietary_tags(recipe)
    )
    diet_term = primary_dietary_search_term(diet)

    queries: List[str] = []

    def with_diet(base: str) -> None:
        if not base:
            return
        queries.append(base)
        if diet_term:
            queries.append(f"{diet_term} {base}")

    if search_name:
        with_diet(f"{search_name} food")
    if search_name and cuisine:
        with_diet(f"{search_name} {cuisine}")
    if search_name and ingredients:
        with_diet(f"{search_name} {' '.join(ingredients)}")
    if cuisine and ingredients:
        with_diet(f"{cuisine} {' '.join(ingredients)} food")
    elif cuisine and len(ingredients) == 1:
        with_diet(f"{cuisine} {ingredients[0]} food")
    elif cuisine:
        with_diet(f"{cuisine} food")

    seen: set[str] = set()
    unique: List[str] = []
    for q in queries:
        key = q.lower()
        if key not in seen:
            seen.add(key)
            unique.append(q)

    if diet_term:
        diet_first = [q for q in unique if q.lower().startswith(diet_term)]
        rest = [q for q in unique if q not in diet_first]
        return diet_first + rest
    return unique


def _score_photo_alt(
    alt: str, recipe_name: str, dietary_tags: Optional[List[str]] = None
) -> int:
    if not alt:
        return 0
    name_tokens = _tokenize(searchable_recipe_name(recipe_name))
    alt_tokens = _tokenize(alt)
    score = len(name_tokens & alt_tokens) if name_tokens else 0
    if dietary_tags:
        for tag in dietary_tags:
            if tag.replace("-", "") in alt.lower() or tag in alt_tokens:
                score += 2
    return score


def _is_allowed_pexels_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme == "https" and parsed.netloc == PEXELS_IMAGE_HOST


def _pick_photo_url(
    photos: List[dict[str, Any]],
    recipe_name: str,
    dietary_tags: Optional[List[str]] = None,
) -> Optional[str]:
    if not photos:
        return None

    candidates = photos
    if dietary_tags:
        filtered = [
            p
            for p in photos
            if not _alt_conflicts_dietary(str(p.get("alt") or ""), dietary_tags)
        ]
        if filtered:
            candidates = filtered
        else:
            logger.info(
                "All Pexels results conflict with dietary_tags=%s for name=%r",
                dietary_tags,
                recipe_name,
            )
            return None

    best = candidates[0]
    best_score = _score_photo_alt(str(best.get("alt") or ""), recipe_name, dietary_tags)

    for photo in candidates[1:]:
        score = _score_photo_alt(str(photo.get("alt") or ""), recipe_name, dietary_tags)
        if score > best_score:
            best = photo
            best_score = score

    src = best.get("src") or {}
    url = src.get("large") or src.get("medium") or src.get("original")
    if isinstance(url, str) and _is_allowed_pexels_url(url):
        photographer = best.get("photographer") or "Unknown"
        logger.info(
            "Pexels image selected id=%s photographer=%s score=%s",
            best.get("id"),
            photographer,
            best_score,
        )
        return url
    return None


def _search_pexels(query: str, api_key: str) -> List[dict[str, Any]]:
    headers = {"Authorization": api_key}
    params = {
        "query": query,
        "per_page": DEFAULT_PER_PAGE,
        "orientation": "landscape",
    }
    with httpx.Client(timeout=REQUEST_TIMEOUT_SEC) as client:
        response = client.get(PEXELS_SEARCH_URL, headers=headers, params=params)
        response.raise_for_status()
        payload = response.json()
    photos = payload.get("photos")
    return photos if isinstance(photos, list) else []


def resolve_recipe_image(
    recipe: Recipe, *, user_dietary_restrictions: Optional[List[str]] = None
) -> Optional[str]:
    """
    Return a Pexels CDN URL for the recipe, or None if disabled / not found.
    """
    if not pexels_enabled():
        logger.debug("Pexels disabled or missing API key")
        return None

    api_key = os.getenv("PEXELS_API_KEY", "").strip()
    recipe_name = recipe.name or ""
    dietary_tags = merge_dietary_context(recipe, user_dietary_restrictions)

    for query in build_search_queries(recipe, dietary_tags=dietary_tags):
        try:
            photos = _search_pexels(query, api_key)
        except httpx.HTTPError as exc:
            logger.warning(
                "Pexels search failed recipe_id=%s query=%r error=%s",
                recipe.id,
                query,
                exc,
            )
            continue

        url = _pick_photo_url(photos, recipe_name, dietary_tags)
        if url:
            logger.info(
                "Resolved image recipe_id=%s query=%r url=%s",
                recipe.id,
                query,
                url,
            )
            return url

    logger.info("No Pexels image for recipe_id=%s name=%r", recipe.id, recipe_name)
    return None


def attach_recipe_image(
    recipe: Recipe,
    db: Session,
    *,
    force: bool = False,
    user_dietary_restrictions: Optional[List[str]] = None,
) -> Optional[str]:
    """
    Set recipe.image_url from Pexels.

    When force=False, skips rows that already have image_url.
    When force=True, re-resolves and overwrites on success; keeps existing URL if
    Pexels returns nothing.
    """
    if not force and recipe.image_url and recipe.image_url.strip():
        return recipe.image_url

    previous = recipe.image_url
    url = resolve_recipe_image(
        recipe, user_dietary_restrictions=user_dietary_restrictions
    )
    if url:
        recipe.image_url = url
        db.add(recipe)
        db.commit()
        db.refresh(recipe)
        return url

    return previous if force and previous else None


def attach_image_if_missing(
    recipe: Recipe,
    db: Session,
    *,
    user_dietary_restrictions: Optional[List[str]] = None,
) -> Optional[str]:
    """Set recipe.image_url from Pexels when empty. Returns final URL or None."""
    return attach_recipe_image(
        recipe,
        db,
        force=False,
        user_dietary_restrictions=user_dietary_restrictions,
    )
