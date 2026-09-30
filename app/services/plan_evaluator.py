"""Deterministic soft evaluator for weekly plan candidates (post-gate).

Never blocks persistence. Produces confidence + reasons safe for client display
(no recipe names or IDs).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from sqlalchemy.orm import Session

from app.models import Recipe, User
from app.services.shopping import parse_measure, pantry_normalized_names


EVALUATOR_KIND = "deterministic_v1"


@dataclass
class PlanEvaluation:
    confidence: str  # high | medium | low
    confidence_reasons: list[str] = field(default_factory=list)
    signals: dict[str, Any] = field(default_factory=dict)
    evaluator_passed: bool = True

    def as_output_json(self) -> dict[str, Any]:
        return {
            "signals": self.signals,
            "confidence": self.confidence,
            "confidence_reasons": list(self.confidence_reasons),
        }

    def as_client_summary(
        self,
        *,
        verification_run_id: str,
        attempt_number: int,
        auto_repaired: bool,
    ) -> dict[str, Any]:
        return {
            "verification_run_id": verification_run_id,
            "attempt_number": int(attempt_number),
            "auto_repaired": bool(auto_repaired),
            "confidence": self.confidence,
            "confidence_reasons": list(self.confidence_reasons),
        }


def _ingredient_rows(raw: Optional[str]) -> list[dict[str, str]]:
    if not raw or not str(raw).strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    rows: list[dict[str, str]] = []
    for item in data:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            measure = str(item.get("measure") or "").strip()
            if name:
                rows.append({"name": name, "measure": measure})
        elif isinstance(item, str) and item.strip():
            rows.append({"name": item.strip(), "measure": ""})
    return rows


def _normalize_name(name: str) -> str:
    return " ".join(str(name).lower().split())


def evaluate_plan_candidates(
    db: Session,
    user: User,
    recipes: Sequence[Recipe],
) -> PlanEvaluation:
    ambiguous = 0
    missing_time = 0
    total_minutes = 0
    distinct_names: set[str] = set()

    for recipe in recipes:
        prep = getattr(recipe, "prep_time_minutes", None)
        cook = getattr(recipe, "cook_time_minutes", None)
        if prep is None and cook is None:
            missing_time += 1
        else:
            total_minutes += int(prep or 0) + int(cook or 0)

        for row in _ingredient_rows(getattr(recipe, "ingredients", None)):
            distinct_names.add(_normalize_name(row["name"]))
            measure = row.get("measure") or ""
            if measure:
                _qty, _unit, needs_review = parse_measure(measure)
                if needs_review:
                    ambiguous += 1

    pantry = pantry_normalized_names(db, user.id)
    matched = len(distinct_names & pantry) if distinct_names else 0
    distinct_count = len(distinct_names)
    coverage = (matched / distinct_count) if distinct_count else 1.0

    reasons: list[str] = []
    confidence = "high"

    if ambiguous >= 3:
        confidence = "low"
        reasons.append("Several ingredient amounts look hard to parse for shopping.")
    elif ambiguous >= 1:
        confidence = "medium"
        reasons.append("Some ingredient amounts may need a quick check when shopping.")

    if missing_time >= 2:
        confidence = "low"
        reasons.append("Multiple recipes are missing prep or cook times.")
    elif missing_time == 1 and confidence == "high":
        confidence = "medium"
        reasons.append("At least one recipe is missing prep or cook time.")

    max_prep = getattr(user, "max_prep_time_minutes", None)
    max_cook = getattr(user, "max_cook_time_minutes", None)
    frequency = int(getattr(user, "frequency", 0) or 0)
    if max_prep is not None and max_cook is not None and frequency > 0:
        budget = frequency * (int(max_prep) + int(max_cook))
        if budget > 0 and total_minutes > budget * 1.25:
            confidence = "low"
            reasons.append("Total prep and cook time looks high vs your weekly caps.")
        elif budget > 0 and total_minutes > budget and confidence == "high":
            confidence = "medium"
            reasons.append("Total prep and cook time is a bit above your weekly caps.")

    if distinct_count > 0 and coverage < 0.15 and confidence == "high":
        confidence = "medium"
        reasons.append("Pantry overlap looks low — expect a fuller shopping list.")

    if not reasons:
        reasons.append("Recipes look complete enough for a confident week.")

    return PlanEvaluation(
        confidence=confidence,
        confidence_reasons=reasons[:4],
        signals={
            "ambiguous_ingredient_count": ambiguous,
            "recipes_missing_time_fields": missing_time,
            "total_active_minutes": total_minutes,
            "pantry_coverage_ratio": round(coverage, 3),
            "pantry_matched_ingredients": matched,
            "pantry_distinct_ingredients": distinct_count,
        },
        evaluator_passed=True,
    )
