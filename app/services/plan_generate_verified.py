"""Shared helpers for gate → optional repair bookkeeping → soft evaluate → persist."""

from __future__ import annotations

import uuid
from typing import Optional, Sequence

from sqlalchemy.orm import Session

from app.models import Recipe, User
from app.schemas import GenerationSummary, WeeklyPlanResponse
from app.services.plan_evaluator import evaluate_plan_candidates_llm_assisted
from app.services.plan_timeline import build_prep_timeline
from app.services.plan_timeline_attach import (
    attach_prep_timeline,
    save_prep_timeline_on_plan,
)
from app.services.plan_verification import PlanVerificationResult, verify_plan_candidates
from app.services.plan_verification_runs import record_plan_verification_run
from app.services.weekly_plan import WeeklyPlanService

MAX_PLAN_GENERATE_ATTEMPTS = 2


def load_and_verify(
    db: Session, user: User, recipe_ids: Sequence[uuid.UUID]
) -> tuple[list[Recipe], PlanVerificationResult]:
    loaded = (
        db.query(Recipe).filter(Recipe.id.in_(list(recipe_ids))).all()
        if recipe_ids
        else []
    )
    return loaded, verify_plan_candidates(user, list(recipe_ids), loaded)


def record_failed_attempt(
    db: Session,
    *,
    user: User,
    week_number: int,
    flow: str,
    gate: PlanVerificationResult,
    candidate_ids: Sequence[uuid.UUID],
    search_attempts: Optional[int],
    generation_attempts: Optional[int],
    attempt_number: int,
) -> uuid.UUID:
    run = record_plan_verification_run(
        db,
        user=user,
        target_week_number=week_number,
        flow=flow,
        gate=gate,
        candidate_ids=candidate_ids,
        search_attempts=search_attempts,
        generation_attempts=generation_attempts,
        attempt_number=attempt_number,
    )
    db.commit()
    return run.id


async def persist_verified_plan(
    *,
    db: Session,
    user: User,
    week_number: int,
    flow: str,
    recipe_ids: Sequence[uuid.UUID],
    recipes: Sequence[Recipe],
    gate: PlanVerificationResult,
    plan_service: WeeklyPlanService,
    search_attempts: Optional[int],
    generation_attempts: Optional[int],
    attempt_number: int,
    auto_repaired: bool,
) -> WeeklyPlanResponse:
    evaluation = evaluate_plan_candidates_llm_assisted(db, user, recipes)
    new_plan = await plan_service.generate_weekly_plan(
        user=user,
        week_number=week_number,
        recipe_ids_from_agent=list(recipe_ids),
        db=db,
    )
    timeline = build_prep_timeline(
        user, recipes, ordered_recipe_ids=list(recipe_ids)
    )
    save_prep_timeline_on_plan(new_plan, timeline)
    run = record_plan_verification_run(
        db,
        user=user,
        target_week_number=week_number,
        flow=flow,
        gate=gate,
        candidate_ids=recipe_ids,
        search_attempts=search_attempts,
        generation_attempts=generation_attempts,
        attempt_number=attempt_number,
        evaluation=evaluation,
        auto_repaired=auto_repaired,
    )
    db.commit()
    response = WeeklyPlanResponse.model_validate(new_plan)
    response.generation_summary = GenerationSummary(
        **evaluation.as_client_summary(
            verification_run_id=str(run.id),
            attempt_number=attempt_number,
            auto_repaired=auto_repaired,
        )
    )
    attach_prep_timeline(
        response,
        user,
        recipes,
        ordered_recipe_ids=list(recipe_ids),
        plan=new_plan,
        prefer_snapshot=True,
    )
    return response
