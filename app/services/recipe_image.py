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


def build_search_queries(recipe: Recipe) -> List[str]:
    raw_name = (recipe.name or "").strip()
    search_name = searchable_recipe_name(raw_name)
    cuisine = (recipe.cuisine or "").strip()
    ingredients = main_ingredient_keywords(recipe.ingredients or "[]", max_count=2)

    queries: List[str] = []

    if search_name:
        queries.append(f"{search_name} food")
    if search_name and cuisine:
        queries.append(f"{search_name} {cuisine}")
    if search_name and ingredients:
        queries.append(f"{search_name} {' '.join(ingredients)}")
    if cuisine and ingredients:
        queries.append(f"{cuisine} {' '.join(ingredients)} food")
    elif cuisine and len(ingredients) == 1:
        queries.append(f"{cuisine} {ingredients[0]} food")
    elif cuisine:
        queries.append(f"{cuisine} food")

    seen: set[str] = set()
    unique: List[str] = []
    for q in queries:
        key = q.lower()
        if key not in seen:
            seen.add(key)
            unique.append(q)
    return unique


def _score_photo_alt(alt: str, recipe_name: str) -> int:
    if not alt:
        return 0
    name_tokens = _tokenize(searchable_recipe_name(recipe_name))
    alt_tokens = _tokenize(alt)
    if not name_tokens:
        return 0
    return len(name_tokens & alt_tokens)


def _is_allowed_pexels_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme == "https" and parsed.netloc == PEXELS_IMAGE_HOST


def _pick_photo_url(photos: List[dict[str, Any]], recipe_name: str) -> Optional[str]:
    if not photos:
        return None

    best = photos[0]
    best_score = _score_photo_alt(str(best.get("alt") or ""), recipe_name)

    for photo in photos[1:]:
        score = _score_photo_alt(str(photo.get("alt") or ""), recipe_name)
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


def resolve_recipe_image(recipe: Recipe) -> Optional[str]:
    """
    Return a Pexels CDN URL for the recipe, or None if disabled / not found.
    """
    if not pexels_enabled():
        logger.debug("Pexels disabled or missing API key")
        return None

    api_key = os.getenv("PEXELS_API_KEY", "").strip()
    recipe_name = recipe.name or ""

    for query in build_search_queries(recipe):
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

        url = _pick_photo_url(photos, recipe_name)
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
    recipe: Recipe, db: Session, *, force: bool = False
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
    url = resolve_recipe_image(recipe)
    if url:
        recipe.image_url = url
        db.add(recipe)
        db.commit()
        db.refresh(recipe)
        return url

    return previous if force and previous else None


def attach_image_if_missing(recipe: Recipe, db: Session) -> Optional[str]:
    """Set recipe.image_url from Pexels when empty. Returns final URL or None."""
    return attach_recipe_image(recipe, db, force=False)
