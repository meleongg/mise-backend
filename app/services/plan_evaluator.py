"""Soft evaluator for weekly plan candidates (post-gate).

Always runs deterministic signals. Optionally layers a structured LLM pass
(`llm_assisted_v1`) for cookability / pantry / shopping notes. Never blocks
persistence and never overrides the hard allergen/diet gate. LLM failures
fail open to deterministic-only results.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Sequence

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.constants import GENERATIVE_MODEL
from app.models import Recipe, User
from app.services.shopping import parse_measure, pantry_normalized_names

logger = logging.getLogger(__name__)

EVALUATOR_KIND = "deterministic_v1"
LLM_ASSISTED_KIND = "llm_assisted_v1"

_CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2}
_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)

PLAN_LLM_EVAL_SYSTEM = (
    "You are an independent soft evaluator for a weekly cooking plan.\n"
    "The hard allergen/diet gate already passed. Do not invent allergens.\n"
    "Return cookability judgment only — never block the plan yourself.\n\n"
    "Rules:\n"
    "- confidence is high | medium | low (prefer medium when unsure).\n"
    "- confidence_reasons: at most 3 short user-facing sentences.\n"
    "- Never include recipe titles, recipe IDs, UUIDs, or personal names.\n"
    "- Speak about the week in aggregate (time load, shopping, pantry).\n"
    "- timeline_feasibility: one short note on prep/cook pacing vs caps.\n"
    "- pantry_coverage_note: one short note on pantry overlap.\n"
    "- shopping_checklist_note: one short note for shopping readiness "
    "(city/retailer when provided; otherwise generic).\n"
    "- Leave a note empty string when you have nothing useful to add.\n"
)


class LlmPlanEvalDraft(BaseModel):
    confidence: Literal["high", "medium", "low"] = "medium"
    confidence_reasons: list[str] = Field(default_factory=list)
    timeline_feasibility: str = ""
    pantry_coverage_note: str = ""
    shopping_checklist_note: str = ""


@dataclass
class PlanEvaluation:
    confidence: str  # high | medium | low
    confidence_reasons: list[str] = field(default_factory=list)
    signals: dict[str, Any] = field(default_factory=dict)
    evaluator_passed: bool = True
    evaluator_kind: str = EVALUATOR_KIND
    evaluator_model_id: Optional[str] = None

    def as_output_json(self) -> dict[str, Any]:
        return {
            "signals": self.signals,
            "confidence": self.confidence,
            "confidence_reasons": list(self.confidence_reasons),
            "evaluator_kind": self.evaluator_kind,
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
            "evaluator_kind": self.evaluator_kind,
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


def _worse_confidence(a: str, b: str) -> str:
    ra = _CONFIDENCE_RANK.get(a, 1)
    rb = _CONFIDENCE_RANK.get(b, 1)
    return a if ra >= rb else b


def _sanitize_reason(text: str, *, limit: int = 160) -> str:
    cleaned = " ".join(str(text or "").split())
    cleaned = _UUID_RE.sub("[id]", cleaned)
    if len(cleaned) > limit:
        cleaned = cleaned[: limit - 1].rstrip() + "…"
    return cleaned


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
        evaluator_kind=EVALUATOR_KIND,
        evaluator_model_id=None,
    )


def _recipe_payloads_for_llm(recipes: Sequence[Recipe]) -> list[dict[str, Any]]:
    """Anonymous recipe cards — no id/name — for the LLM evaluator."""
    cards: list[dict[str, Any]] = []
    for idx, recipe in enumerate(recipes, start=1):
        ingredients = [
            {"name": row["name"], "measure": row.get("measure") or ""}
            for row in _ingredient_rows(getattr(recipe, "ingredients", None))
        ]
        cards.append(
            {
                "slot": idx,
                "cuisine": getattr(recipe, "cuisine", None),
                "difficulty": getattr(recipe, "difficulty", None),
                "prep_time_minutes": getattr(recipe, "prep_time_minutes", None),
                "cook_time_minutes": getattr(recipe, "cook_time_minutes", None),
                "ingredient_count": len(ingredients),
                "ingredients": ingredients[:20],
            }
        )
    return cards


def _build_llm_eval_messages(
    user: User,
    recipes: Sequence[Recipe],
    base: PlanEvaluation,
) -> list[Any]:
    profile = {
        "frequency": getattr(user, "frequency", None),
        "max_prep_time_minutes": getattr(user, "max_prep_time_minutes", None),
        "max_cook_time_minutes": getattr(user, "max_cook_time_minutes", None),
        "skill_level": getattr(user, "skill_level", None),
        "cuisine": getattr(user, "cuisine", None),
        "city": getattr(user, "city", None),
        "preferred_retailer": getattr(user, "preferred_retailer", None),
    }
    payload = {
        "cook_profile": profile,
        "deterministic_signals": base.signals,
        "deterministic_confidence": base.confidence,
        "deterministic_reasons": list(base.confidence_reasons),
        "recipes": _recipe_payloads_for_llm(recipes),
    }
    return [
        SystemMessage(content=PLAN_LLM_EVAL_SYSTEM),
        HumanMessage(
            content=(
                "Evaluate this weekly plan candidate (JSON).\n"
                f"{json.dumps(payload, indent=2, default=str)}"
            )
        ),
    ]


def invoke_llm_plan_eval(
    user: User,
    recipes: Sequence[Recipe],
    base: PlanEvaluation,
) -> LlmPlanEvalDraft:
    """Structured LLM soft eval. Callers must handle exceptions (fail-open)."""
    llm = ChatOpenAI(model=GENERATIVE_MODEL, temperature=0)
    structured = llm.with_structured_output(LlmPlanEvalDraft)
    draft = structured.invoke(_build_llm_eval_messages(user, recipes, base))
    if not isinstance(draft, LlmPlanEvalDraft):
        draft = LlmPlanEvalDraft.model_validate(draft)
    return draft


def _merge_llm_draft(base: PlanEvaluation, draft: LlmPlanEvalDraft) -> PlanEvaluation:
    llm_reasons = [
        _sanitize_reason(r)
        for r in (draft.confidence_reasons or [])
        if str(r or "").strip()
    ]
    for note in (
        draft.timeline_feasibility,
        draft.pantry_coverage_note,
        draft.shopping_checklist_note,
    ):
        cleaned = _sanitize_reason(note)
        if cleaned and cleaned not in llm_reasons:
            llm_reasons.append(cleaned)

    merged_reasons: list[str] = []
    for reason in list(base.confidence_reasons) + llm_reasons:
        if reason and reason not in merged_reasons:
            merged_reasons.append(reason)

    llm_confidence = draft.confidence if draft.confidence in _CONFIDENCE_RANK else "medium"
    confidence = _worse_confidence(base.confidence, llm_confidence)

    signals = dict(base.signals)
    signals["llm_confidence"] = llm_confidence
    signals["llm_timeline_feasibility"] = _sanitize_reason(
        draft.timeline_feasibility, limit=200
    )
    signals["llm_pantry_coverage_note"] = _sanitize_reason(
        draft.pantry_coverage_note, limit=200
    )
    signals["llm_shopping_checklist_note"] = _sanitize_reason(
        draft.shopping_checklist_note, limit=200
    )

    return PlanEvaluation(
        confidence=confidence,
        confidence_reasons=merged_reasons[:4],
        signals=signals,
        evaluator_passed=True,
        evaluator_kind=LLM_ASSISTED_KIND,
        evaluator_model_id=GENERATIVE_MODEL,
    )


def evaluate_plan_candidates_llm_assisted(
    db: Session,
    user: User,
    recipes: Sequence[Recipe],
    *,
    llm_invoke=None,
) -> PlanEvaluation:
    """Deterministic soft eval, then optional LLM assist (fail-open)."""
    base = evaluate_plan_candidates(db, user, recipes)
    invoke = llm_invoke or invoke_llm_plan_eval
    try:
        draft = invoke(user, recipes, base)
    except Exception:
        logger.exception("LLM plan evaluator failed; using deterministic_v1 only")
        return base
    if not isinstance(draft, LlmPlanEvalDraft):
        try:
            draft = LlmPlanEvalDraft.model_validate(draft)
        except Exception:
            logger.exception("LLM plan evaluator returned invalid draft")
            return base
    return _merge_llm_draft(base, draft)
