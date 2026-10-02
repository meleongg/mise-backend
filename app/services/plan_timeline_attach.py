"""Attach and optionally persist a prep timeline on WeeklyPlanResponse."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional, Sequence
from uuid import UUID

from app.models import Recipe, User, WeeklyPlan
from app.schemas import PrepTimelineResponse, WeeklyPlanResponse
from app.services.plan_timeline import PrepTimeline, build_prep_timeline
from app.services.weekly_plan import (
    list_plan_entries,
    ordered_catalog_uuids_from_entries,
)


def timeline_to_storage_dict(
    timeline: PrepTimeline,
    *,
    snapshotted_at: Optional[datetime] = None,
) -> dict:
    payload = timeline.as_dict()
    payload["snapshotted_at"] = (
        (snapshotted_at or datetime.now(timezone.utc)).isoformat()
    )
    return payload


def timeline_to_json(timeline: PrepTimeline) -> str:
    return json.dumps(timeline_to_storage_dict(timeline))


def parse_stored_timeline(raw: Optional[str]) -> Optional[dict]:
    if not raw or not str(raw).strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return None
    return data


def save_prep_timeline_on_plan(plan: WeeklyPlan, timeline: PrepTimeline) -> str:
    """Serialize timeline onto the plan row (caller commits)."""
    raw = timeline_to_json(timeline)
    plan.prep_timeline_json = raw
    return raw


def _ordered_ids_from_response(
    response: WeeklyPlanResponse,
    ordered_recipe_ids: Optional[Sequence[UUID]],
) -> Optional[list[UUID]]:
    if ordered_recipe_ids is not None:
        return list(ordered_recipe_ids)
    recipes = getattr(response, "recipes", None) or []
    if recipes:
        return [r.id for r in recipes]
    entries = getattr(response, "entries", None) or []
    if entries:
        ordered = sorted(entries, key=lambda e: e.position)
        return [
            e.catalog_recipe_id
            for e in ordered
            if getattr(e, "catalog_recipe_id", None) is not None
        ]
    return None


def attach_prep_timeline(
    response: WeeklyPlanResponse,
    user: User,
    recipes: Sequence[Recipe],
    *,
    ordered_recipe_ids: Optional[Sequence[UUID]] = None,
    plan: Optional[WeeklyPlan] = None,
    prefer_snapshot: bool = True,
    persist_if_missing: bool = False,
) -> WeeklyPlanResponse:
    """Attach timeline from snapshot when present, otherwise compute."""
    if prefer_snapshot and plan is not None:
        stored = parse_stored_timeline(getattr(plan, "prep_timeline_json", None))
        if stored is not None:
            payload = dict(stored)
            payload["source"] = "snapshot"
            response.prep_timeline = PrepTimelineResponse.model_validate(payload)
            return response

    order = _ordered_ids_from_response(response, ordered_recipe_ids)
    timeline = build_prep_timeline(user, recipes, ordered_recipe_ids=order)
    if persist_if_missing and plan is not None:
        save_prep_timeline_on_plan(plan, timeline)
        stored = parse_stored_timeline(plan.prep_timeline_json)
        assert stored is not None
        payload = dict(stored)
        payload["source"] = "snapshot"
        response.prep_timeline = PrepTimelineResponse.model_validate(payload)
        return response

    payload = timeline.as_dict()
    payload["source"] = "computed"
    response.prep_timeline = PrepTimelineResponse.model_validate(payload)
    return response


def rebuild_and_save_prep_timeline(
    plan: WeeklyPlan,
    user: User,
    recipes: Sequence[Recipe],
    *,
    ordered_recipe_ids: Optional[Sequence[UUID]] = None,
    db=None,
) -> PrepTimeline:
    """Recompute timeline for a mutated plan and write the snapshot column."""
    order = list(ordered_recipe_ids) if ordered_recipe_ids is not None else None
    if order is None and db is not None:
        order = ordered_catalog_uuids_from_entries(list_plan_entries(plan, db))
    timeline = build_prep_timeline(user, recipes, ordered_recipe_ids=order)
    save_prep_timeline_on_plan(plan, timeline)
    return timeline
