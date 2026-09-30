"""Deterministic pre-persist gate for weekly plan recipe candidates.

Hard failures block plan commit. Never invents evaluator judgments; allergen
and dietary rules mirror catalog search filter semantics.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from app.models import Recipe, User
from app.utils.recipe_search_filters import normalize_preference_tags


@dataclass
class VerificationFailure:
    code: str
    message: str
    recipe_id: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.recipe_id:
            payload["recipe_id"] = self.recipe_id
        return payload


@dataclass
class PlanVerificationResult:
    ok: bool
    failures: list[VerificationFailure] = field(default_factory=list)

    def as_detail(self) -> dict[str, Any]:
        return {
            "code": "plan_verification_failed",
            "message": (
                "This plan could not be verified against your preferences. "
                "No recipes were saved. Try generating again."
            ),
            "failures": [f.as_dict() for f in self.failures],
        }

    def failure_codes(self) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for failure in self.failures:
            if failure.code in seen:
                continue
            seen.add(failure.code)
            ordered.append(failure.code)
        return ordered

    def as_client_detail(
        self, *, verification_run_id: Optional[str] = None
    ) -> dict[str, Any]:
        """HTTP-safe detail: codes only, never recipe IDs or names."""
        payload: dict[str, Any] = {
            "code": "plan_verification_failed",
            "message": (
                "This plan could not be verified against your preferences. "
                "No recipes were saved. Try generating again."
            ),
            "failure_codes": self.failure_codes(),
        }
        if verification_run_id:
            payload["verification_run_id"] = verification_run_id
        return payload


def _parse_json_list(raw: Optional[str]) -> list[str]:
    if not raw or not str(raw).strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [str(item).strip().lower() for item in data if str(item).strip()]


def _ingredients_complete(raw: Optional[str]) -> bool:
    if not raw or not str(raw).strip():
        return False
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return False
    if not isinstance(data, list) or len(data) == 0:
        return False
    for item in data:
        if isinstance(item, str) and item.strip():
            return True
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            if name:
                return True
    return False


def verify_plan_candidates(
    user: User,
    recipe_ids: list[uuid.UUID],
    recipes: list[Recipe],
) -> PlanVerificationResult:
    """
    Validate agent-selected recipes before weekly plan persistence.

    Missing IDs, wrong count, incomplete content, allergen overlap, missing
    allergen/diet metadata when the user has constraints, dietary tag gaps,
    and prep/cook over user caps are hard failures.
    """
    failures: list[VerificationFailure] = []
    by_id = {r.id: r for r in recipes}
    expected = int(getattr(user, "frequency", 0) or 0)

    if expected > 0 and len(recipe_ids) != expected:
        failures.append(
            VerificationFailure(
                code="recipe_count_mismatch",
                message=(
                    f"Expected {expected} recipes for this week, "
                    f"got {len(recipe_ids)}."
                ),
            )
        )

    missing = [rid for rid in recipe_ids if rid not in by_id]
    for rid in missing:
        failures.append(
            VerificationFailure(
                code="recipe_missing",
                message="A selected recipe could not be loaded.",
                recipe_id=str(rid),
            )
        )

    user_allergens = normalize_preference_tags(
        _parse_json_list(getattr(user, "allergens", None))
    )
    user_diet = normalize_preference_tags(
        _parse_json_list(getattr(user, "dietary_restrictions", None))
    )
    max_prep = getattr(user, "max_prep_time_minutes", None)
    max_cook = getattr(user, "max_cook_time_minutes", None)

    for rid in recipe_ids:
        recipe = by_id.get(rid)
        if recipe is None:
            continue
        rid_str = str(rid)

        if not _ingredients_complete(getattr(recipe, "ingredients", None)):
            failures.append(
                VerificationFailure(
                    code="incomplete_ingredients",
                    message="A recipe is missing usable ingredients.",
                    recipe_id=rid_str,
                )
            )

        instructions = (getattr(recipe, "instructions", None) or "").strip()
        if not instructions:
            failures.append(
                VerificationFailure(
                    code="incomplete_instructions",
                    message="A recipe is missing instructions.",
                    recipe_id=rid_str,
                )
            )

        recipe_allergens = normalize_preference_tags(
            _parse_json_list(getattr(recipe, "allergens", None))
        )
        if user_allergens:
            raw_allergens = getattr(recipe, "allergens", None)
            if not raw_allergens or not str(raw_allergens).strip():
                failures.append(
                    VerificationFailure(
                        code="missing_allergen_metadata",
                        message=(
                            "A recipe is missing allergen metadata required "
                            "for your avoid list."
                        ),
                        recipe_id=rid_str,
                    )
                )
            elif set(recipe_allergens) & set(user_allergens):
                failures.append(
                    VerificationFailure(
                        code="allergen_conflict",
                        message=(
                            "A recipe conflicts with your allergen avoid list."
                        ),
                        recipe_id=rid_str,
                    )
                )

        recipe_diet = normalize_preference_tags(
            _parse_json_list(getattr(recipe, "dietary_tags", None))
        )
        if user_diet:
            raw_diet = getattr(recipe, "dietary_tags", None)
            if not raw_diet or not str(raw_diet).strip():
                failures.append(
                    VerificationFailure(
                        code="missing_dietary_metadata",
                        message=(
                            "A recipe is missing dietary tags required for "
                            "your restrictions."
                        ),
                        recipe_id=rid_str,
                    )
                )
            elif not set(user_diet).issubset(set(recipe_diet)):
                failures.append(
                    VerificationFailure(
                        code="dietary_conflict",
                        message=(
                            "A recipe does not satisfy your dietary "
                            "restrictions."
                        ),
                        recipe_id=rid_str,
                    )
                )

        if max_prep is not None and recipe.prep_time_minutes is not None:
            if int(recipe.prep_time_minutes) > int(max_prep):
                failures.append(
                    VerificationFailure(
                        code="prep_time_exceeded",
                        message="A recipe exceeds your max prep time.",
                        recipe_id=rid_str,
                    )
                )
        if max_cook is not None and recipe.cook_time_minutes is not None:
            if int(recipe.cook_time_minutes) > int(max_cook):
                failures.append(
                    VerificationFailure(
                        code="cook_time_exceeded",
                        message="A recipe exceeds your max cook time.",
                        recipe_id=rid_str,
                    )
                )

    return PlanVerificationResult(ok=len(failures) == 0, failures=failures)
