"""Attach a computed prep timeline onto a WeeklyPlanResponse."""

from __future__ import annotations

import json
from typing import Optional, Sequence
from uuid import UUID

from app.models import Recipe, User
from app.schemas import PrepTimelineResponse, WeeklyPlanResponse
from app.services.plan_timeline import build_prep_timeline
from app.services.weekly_plan import parse_recipe_schedule


def attach_prep_timeline(
    response: WeeklyPlanResponse,
    user: User,
    recipes: Sequence[Recipe],
    *,
    ordered_recipe_ids: Optional[Sequence[UUID]] = None,
) -> WeeklyPlanResponse:
    order = list(ordered_recipe_ids) if ordered_recipe_ids is not None else None
    if order is None and getattr(response, "recipe_schedule", None):
        try:
            order = [
                UUID(str(x)) for x in parse_recipe_schedule(response.recipe_schedule)
            ]
        except (TypeError, ValueError, json.JSONDecodeError, KeyError):
            order = None
    timeline = build_prep_timeline(user, recipes, ordered_recipe_ids=order)
    response.prep_timeline = PrepTimelineResponse.model_validate(timeline.as_dict())
    return response
