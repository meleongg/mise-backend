"""Normalize and format recipe ingredients for storage and display."""

from __future__ import annotations

import re

_TO_TASTE = re.compile(r"^to\s*taste$", re.IGNORECASE)
_TRAILING_COMMA = re.compile(r",\s*$")
_QUANTITY_COMMA_PREP = re.compile(r"^(\d+)\s*,\s+(.+)$")


def _clean_measure(amount: str) -> str:
    amount = (amount or "").strip()
    amount = _TRAILING_COMMA.sub("", amount)
    match = _QUANTITY_COMMA_PREP.match(amount)
    if match:
        amount = f"{match.group(1)} {match.group(2)}"
    return amount


def normalize_ingredient_pair(name: str, measure: str) -> tuple[str, str]:
    """Clean LLM output before persisting."""
    ingredient_name = (name or "").strip()
    amount = _clean_measure(measure)

    if _TO_TASTE.match(amount):
        return ingredient_name, "to taste"

    return ingredient_name, amount


def format_ingredient_display(name: str, measure: str = "") -> str:
    """Human-readable line for UI (matches frontend formatIngredient.ts)."""
    ingredient_name, amount = normalize_ingredient_pair(name, measure)

    if not ingredient_name and not amount:
        return ""
    if not amount:
        return ingredient_name
    if not ingredient_name:
        return amount

    if _TO_TASTE.match(amount):
        return f"{ingredient_name}, to taste"

    amount = re.sub(r"^(\d+)\s*,\s*$", r"\1", amount)
    return f"{amount} {ingredient_name}".strip()
