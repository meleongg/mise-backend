"""Deterministic personal-recipe and Sodie proposal services."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import (
    PersonalRecipe,
    PersonalRecipeRevision,
    Recipe,
    SodieActionProposal,
    SodieThread,
    User,
    UserRecipeProgress,
)
from app.schemas.sodie_proposals import RecipeEditPatch


def _json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _json_loads(value: Optional[str], default: Any = None, *, passthrough: bool = False) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        if passthrough:
            return value
        return default


def recipe_content_snapshot(recipe: Recipe) -> Dict[str, Any]:
    return {
        "title": recipe.name,
        "servings": recipe.portion_size,
        "ingredients": _json_loads(recipe.ingredients, []),
        "instructions": _json_loads(recipe.instructions, recipe.instructions, passthrough=True),
        "notes": None,
        "metadata": {
            "cuisine": recipe.cuisine,
            "difficulty": recipe.difficulty,
            "dietary_tags": _json_loads(recipe.dietary_tags, None),
            "allergens": _json_loads(recipe.allergens, None),
        },
    }


def personal_content_snapshot(personal: PersonalRecipe) -> Dict[str, Any]:
    return {
        "title": personal.name,
        "servings": personal.portion_size,
        "ingredients": _json_loads(personal.ingredients, []),
        "instructions": _json_loads(personal.instructions, personal.instructions),
        "notes": personal.notes,
        "metadata": _json_loads(personal.metadata_json, {}),
    }


def content_hash(snapshot: Dict[str, Any]) -> str:
    return hashlib.sha256(_json_dumps(snapshot).encode("utf-8")).hexdigest()


def apply_patch(base: Dict[str, Any], patch: RecipeEditPatch) -> Dict[str, Any]:
    data = dict(base)
    updates = patch.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=422, detail="Proposal patch must change at least one field")
    unknown = set(updates) - {
        "title",
        "servings",
        "ingredients",
        "instructions",
        "notes",
        "metadata",
    }
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unsupported patch fields: {sorted(unknown)}")
    data.update(updates)
    return data


def build_diff(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    changed = {}
    for key in ("title", "servings", "ingredients", "instructions", "notes", "metadata"):
        if before.get(key) != after.get(key):
            changed[key] = {"before": before.get(key), "after": after.get(key)}
    return {"fields": changed}


def build_impact_preview() -> Dict[str, Any]:
    """Shopping/plan entry impact is deferred until weekly_plan_entries lands."""
    return {
        "serving_text": "Personal copy servings update on approve; catalog and plan schedule unchanged",
        "plan_schedule": "unchanged",
        "shopping_list": "deferred",
        "list_reconciliation_queued": False,
    }


def serialize_personal_recipe(personal: PersonalRecipe) -> Dict[str, Any]:
    meta = _json_loads(personal.metadata_json, {}) or {}
    source = personal.source_recipe
    return {
        "id": personal.id,
        "source_recipe_id": personal.source_recipe_id,
        "name": personal.name,
        "ingredients": _json_loads(personal.ingredients, []),
        "instructions": _json_loads(personal.instructions, personal.instructions),
        "portion_size": personal.portion_size,
        "notes": personal.notes,
        "metadata": meta,
        "current_revision": personal.current_revision,
        "is_active": personal.is_active,
        "created_at": personal.created_at,
        "updated_at": personal.updated_at,
        # Catalog chrome for display until personal copies own their own media.
        "image_url": getattr(source, "image_url", None) if source else None,
        "cuisine": getattr(source, "cuisine", None) if source else meta.get("cuisine"),
        "dietary_tags": meta.get("dietary_tags")
        or (_json_loads(getattr(source, "dietary_tags", None), None) if source else None),
        "allergens": meta.get("allergens")
        or (_json_loads(getattr(source, "allergens", None), None) if source else None),
    }


def serialize_proposal(proposal: SodieActionProposal) -> Dict[str, Any]:
    return {
        "id": proposal.id,
        "thread_id": proposal.thread_id,
        "action_type": proposal.action_type,
        "status": proposal.status,
        "source_recipe_id": proposal.source_recipe_id,
        "personal_recipe_id": proposal.personal_recipe_id,
        "payload": _json_loads(proposal.payload_json, {}),
        "diff": _json_loads(proposal.diff_json, {}),
        "impact": _json_loads(proposal.impact_json, {}),
        "rationale": proposal.rationale,
        "idempotency_key": proposal.idempotency_key,
        "created_at": proposal.created_at,
        "updated_at": proposal.updated_at,
        "applied_at": proposal.applied_at,
    }


def _get_owned_proposal(db: Session, proposal_id: UUID, user_id: UUID) -> SodieActionProposal:
    proposal = (
        db.query(SodieActionProposal)
        .filter(SodieActionProposal.id == proposal_id, SodieActionProposal.user_id == user_id)
        .first()
    )
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")
    return proposal


def propose_recipe_edit(
    db: Session,
    user: User,
    *,
    source_recipe_id: UUID,
    patch: RecipeEditPatch,
    idempotency_key: str,
    rationale: Optional[str] = None,
    thread_id: Optional[UUID] = None,
) -> SodieActionProposal:
    existing = (
        db.query(SodieActionProposal)
        .filter(
            SodieActionProposal.user_id == user.id,
            SodieActionProposal.idempotency_key == idempotency_key,
        )
        .first()
    )
    if existing:
        return existing

    recipe = db.query(Recipe).filter(Recipe.id == source_recipe_id).first()
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")

    before = recipe_content_snapshot(recipe)
    after = apply_patch(before, patch)
    diff = build_diff(before, after)
    if not diff["fields"]:
        raise HTTPException(status_code=422, detail="Proposal patch must change at least one field")

    proposal = SodieActionProposal(
        user_id=user.id,
        thread_id=thread_id,
        action_type="propose_recipe_edit",
        status="pending",
        source_recipe_id=recipe.id,
        payload_json=_json_dumps({"before": before, "after": after, "patch": patch.model_dump(exclude_none=True)}),
        diff_json=_json_dumps(diff),
        impact_json=_json_dumps(build_impact_preview()),
        rationale=rationale,
        idempotency_key=idempotency_key,
        source_content_hash=content_hash(before),
    )
    db.add(proposal)
    db.commit()
    db.refresh(proposal)
    return proposal


def reject_proposal(db: Session, user: User, proposal_id: UUID) -> SodieActionProposal:
    proposal = _get_owned_proposal(db, proposal_id, user.id)
    if proposal.status == "rejected":
        return proposal
    if proposal.status != "pending":
        raise HTTPException(status_code=409, detail=f"Cannot reject proposal in status {proposal.status}")
    proposal.status = "rejected"
    proposal.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(proposal)
    return proposal


def apply_recipe_edit(db: Session, user: User, proposal_id: UUID) -> SodieActionProposal:
    proposal = _get_owned_proposal(db, proposal_id, user.id)
    if proposal.status == "applied":
        return proposal
    if proposal.status != "pending":
        raise HTTPException(status_code=409, detail=f"Cannot approve proposal in status {proposal.status}")

    if not proposal.source_recipe_id:
        raise HTTPException(status_code=422, detail="Proposal is missing source_recipe_id")

    recipe = db.query(Recipe).filter(Recipe.id == proposal.source_recipe_id).first()
    if not recipe:
        raise HTTPException(status_code=404, detail="Source recipe not found")

    current = recipe_content_snapshot(recipe)
    if content_hash(current) != proposal.source_content_hash:
        proposal.status = "expired"
        proposal.updated_at = datetime.now(timezone.utc)
        db.commit()
        raise HTTPException(status_code=409, detail="Proposal is stale relative to the catalog recipe")

    payload = _json_loads(proposal.payload_json, {})
    after = payload.get("after")
    if not isinstance(after, dict):
        raise HTTPException(status_code=500, detail="Proposal payload is invalid")

    now = datetime.now(timezone.utc)
    # One active personal recipe per user + catalog source; further Approves bump revision.
    personal = (
        db.query(PersonalRecipe)
        .filter(
            PersonalRecipe.user_id == user.id,
            PersonalRecipe.source_recipe_id == recipe.id,
            PersonalRecipe.is_active.is_(True),
        )
        .order_by(PersonalRecipe.updated_at.desc())
        .first()
    )

    instructions_value = (
        _json_dumps(after["instructions"])
        if not isinstance(after.get("instructions"), str)
        else after["instructions"]
    )
    # Preserve catalog dietary/image lineage in metadata when the patch did not set it.
    meta = after.get("metadata") if isinstance(after.get("metadata"), dict) else {}
    if not meta:
        meta = current.get("metadata") or {}

    if personal:
        personal.name = after["title"]
        personal.ingredients = _json_dumps(after.get("ingredients", []))
        personal.instructions = instructions_value
        personal.portion_size = after.get("servings")
        personal.notes = after.get("notes")
        personal.metadata_json = _json_dumps(meta)
        personal.current_revision = int(personal.current_revision or 0) + 1
        personal.updated_at = now
        revision_number = personal.current_revision
    else:
        personal = PersonalRecipe(
            user_id=user.id,
            source_recipe_id=recipe.id,
            name=after["title"],
            ingredients=_json_dumps(after.get("ingredients", [])),
            instructions=instructions_value,
            portion_size=after.get("servings"),
            notes=after.get("notes"),
            metadata_json=_json_dumps(meta),
            current_revision=1,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db.add(personal)
        db.flush()
        revision_number = 1

    revision = PersonalRecipeRevision(
        personal_recipe_id=personal.id,
        revision_number=revision_number,
        content_snapshot=_json_dumps(after),
        structured_diff=proposal.diff_json,
        rationale=proposal.rationale,
        actor_user_id=user.id,
        created_at=now,
    )
    db.add(revision)

    proposal.status = "applied"
    proposal.personal_recipe_id = personal.id
    proposal.applied_at = now
    proposal.updated_at = now
    # Catalog recipe must remain untouched; no writes to `recipe` here.
    db.commit()
    db.refresh(proposal)
    return proposal


def preference_snapshot(user: User) -> Dict[str, Any]:
    return {
        "max_prep_time_minutes": getattr(user, "max_prep_time_minutes", None),
        "max_cook_time_minutes": getattr(user, "max_cook_time_minutes", None),
        "preferred_portion_size": getattr(user, "preferred_portion_size", None),
        "recipe_repeat_preference": getattr(user, "recipe_repeat_preference", None)
        or "standard",
    }


def propose_preference_tweak(
    db: Session,
    user: User,
    *,
    patch: Dict[str, Any],
    idempotency_key: str,
    rationale: Optional[str] = None,
    thread_id: Optional[UUID] = None,
) -> SodieActionProposal:
    existing = (
        db.query(SodieActionProposal)
        .filter(
            SodieActionProposal.user_id == user.id,
            SodieActionProposal.idempotency_key == idempotency_key,
        )
        .first()
    )
    if existing:
        return existing

    before = preference_snapshot(user)
    allow = (
        "max_prep_time_minutes",
        "max_cook_time_minutes",
        "preferred_portion_size",
        "recipe_repeat_preference",
    )
    clean_patch = {k: patch[k] for k in allow if k in patch and patch[k] is not None}
    if not clean_patch:
        raise HTTPException(status_code=422, detail="Preference patch is empty")

    after = {**before, **clean_patch}
    diff_fields = {
        key: {"before": before.get(key), "after": after.get(key)}
        for key in clean_patch
        if before.get(key) != after.get(key)
    }
    if not diff_fields:
        raise HTTPException(status_code=422, detail="Preference patch makes no changes")

    proposal = SodieActionProposal(
        user_id=user.id,
        thread_id=thread_id,
        action_type="propose_preference_tweak",
        status="pending",
        source_recipe_id=None,
        payload_json=_json_dumps(
            {"before": before, "after": after, "patch": clean_patch}
        ),
        diff_json=_json_dumps({"fields": diff_fields}),
        impact_json=_json_dumps(
            {
                "serving_text": "preference only",
                "plan_schedule": "unchanged until next generation",
                "shopping_list": "unchanged",
                "list_reconciliation_queued": False,
            }
        ),
        rationale=rationale,
        idempotency_key=idempotency_key,
        source_content_hash=content_hash(before),
    )
    db.add(proposal)
    db.commit()
    db.refresh(proposal)
    return proposal


def apply_preference_tweak(
    db: Session, user: User, proposal_id: UUID
) -> SodieActionProposal:
    proposal = _get_owned_proposal(db, proposal_id, user.id)
    if proposal.status == "applied":
        return proposal
    if proposal.status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot approve proposal in status {proposal.status}",
        )
    if proposal.action_type != "propose_preference_tweak":
        raise HTTPException(status_code=422, detail="Not a preference proposal")

    current = preference_snapshot(user)
    if content_hash(current) != proposal.source_content_hash:
        proposal.status = "expired"
        proposal.updated_at = datetime.now(timezone.utc)
        db.commit()
        raise HTTPException(
            status_code=409,
            detail="Proposal is stale relative to current preferences",
        )

    payload = _json_loads(proposal.payload_json, {})
    after = payload.get("after")
    if not isinstance(after, dict):
        raise HTTPException(status_code=500, detail="Proposal payload is invalid")

    for key in (
        "max_prep_time_minutes",
        "max_cook_time_minutes",
        "preferred_portion_size",
        "recipe_repeat_preference",
    ):
        if key in after:
            setattr(user, key, after[key])

    now = datetime.now(timezone.utc)
    proposal.status = "applied"
    proposal.applied_at = now
    proposal.updated_at = now
    db.commit()
    db.refresh(proposal)
    return proposal


def list_recipe_pick_candidates(
    db: Session, user: User, *, limit: int = 12
) -> List[Dict[str, Any]]:
    """Server-built catalog shortlist for Tips recipe suggestions."""
    completed_ids = {
        row.recipe_id
        for row in db.query(UserRecipeProgress.recipe_id)
        .filter(
            UserRecipeProgress.user_id == user.id,
            UserRecipeProgress.status == "completed",
        )
        .all()
        if row.recipe_id is not None
    }

    preferred_cuisine = (user.cuisine or "").strip()
    recipes: List[Recipe] = []
    if preferred_cuisine:
        matching = (
            db.query(Recipe)
            .filter(Recipe.cuisine.ilike(preferred_cuisine))
            .limit(limit)
            .all()
        )
        if len(matching) < limit:
            extras = (
                db.query(Recipe)
                .filter(~Recipe.cuisine.ilike(preferred_cuisine))
                .limit(limit - len(matching))
                .all()
            )
            matching = matching + extras
        recipes = matching
    else:
        recipes = db.query(Recipe).limit(limit).all()

    not_done = [r for r in recipes if r.id not in completed_ids]
    if len(not_done) >= min(4, limit):
        recipes = not_done[:limit]

    max_cook = getattr(user, "max_cook_time_minutes", None)
    max_prep = getattr(user, "max_prep_time_minutes", None)
    filtered: List[Recipe] = []
    for recipe in recipes:
        if max_cook is not None and recipe.cook_time_minutes is not None:
            if recipe.cook_time_minutes > max_cook:
                continue
        if max_prep is not None and recipe.prep_time_minutes is not None:
            if recipe.prep_time_minutes > max_prep:
                continue
        filtered.append(recipe)
    if filtered:
        recipes = filtered[:limit]

    return [
        {
            "id": str(recipe.id),
            "name": recipe.name,
            "cuisine": recipe.cuisine,
            "difficulty": recipe.difficulty,
            "prep_time_minutes": recipe.prep_time_minutes,
            "cook_time_minutes": recipe.cook_time_minutes,
        }
        for recipe in recipes[:limit]
    ]


def propose_recipe_pick(
    db: Session,
    user: User,
    *,
    recipe_id: UUID,
    idempotency_key: str,
    rationale: Optional[str] = None,
    thread_id: Optional[UUID] = None,
) -> SodieActionProposal:
    existing = (
        db.query(SodieActionProposal)
        .filter(
            SodieActionProposal.user_id == user.id,
            SodieActionProposal.idempotency_key == idempotency_key,
        )
        .first()
    )
    if existing:
        return existing

    recipe = db.query(Recipe).filter(Recipe.id == recipe_id).first()
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")

    after = {
        "id": str(recipe.id),
        "name": recipe.name,
        "cuisine": recipe.cuisine,
        "difficulty": recipe.difficulty,
        "prep_time_minutes": recipe.prep_time_minutes,
        "cook_time_minutes": recipe.cook_time_minutes,
    }
    proposal = SodieActionProposal(
        user_id=user.id,
        thread_id=thread_id,
        action_type="propose_recipe_pick",
        status="pending",
        source_recipe_id=recipe.id,
        payload_json=_json_dumps(
            {"before": None, "after": after, "recipe_id": str(recipe.id)}
        ),
        diff_json=_json_dumps(
            {
                "fields": {
                    "recipe": {"before": None, "after": after.get("name")},
                    "cuisine": {"before": None, "after": after.get("cuisine")},
                    "difficulty": {"before": None, "after": after.get("difficulty")},
                }
            }
        ),
        impact_json=_json_dumps(
            {
                "serving_text": "open catalog recipe",
                "plan_schedule": "unchanged until weekly_plan_entries",
                "shopping_list": "unchanged",
                "list_reconciliation_queued": False,
            }
        ),
        rationale=rationale,
        idempotency_key=idempotency_key,
        source_content_hash=content_hash({"recipe_id": str(recipe.id)}),
    )
    db.add(proposal)
    db.commit()
    db.refresh(proposal)
    return proposal


def apply_recipe_pick(
    db: Session, user: User, proposal_id: UUID
) -> SodieActionProposal:
    """Approve a Tips recipe pick — no plan mutation; FE opens the catalog recipe."""
    proposal = _get_owned_proposal(db, proposal_id, user.id)
    if proposal.status == "applied":
        return proposal
    if proposal.status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot approve proposal in status {proposal.status}",
        )
    if proposal.action_type != "propose_recipe_pick":
        raise HTTPException(status_code=422, detail="Not a recipe pick proposal")
    if not proposal.source_recipe_id:
        raise HTTPException(status_code=500, detail="Recipe pick missing source recipe")

    recipe = db.query(Recipe).filter(Recipe.id == proposal.source_recipe_id).first()
    if not recipe:
        proposal.status = "expired"
        proposal.updated_at = datetime.now(timezone.utc)
        db.commit()
        raise HTTPException(
            status_code=409, detail="Suggested recipe is no longer available"
        )

    now = datetime.now(timezone.utc)
    proposal.status = "applied"
    proposal.applied_at = now
    proposal.updated_at = now
    db.commit()
    db.refresh(proposal)
    return proposal


def apply_proposal(db: Session, user: User, proposal_id: UUID) -> SodieActionProposal:
    proposal = _get_owned_proposal(db, proposal_id, user.id)
    if proposal.action_type == "propose_preference_tweak":
        return apply_preference_tweak(db, user, proposal_id)
    if proposal.action_type == "propose_recipe_pick":
        return apply_recipe_pick(db, user, proposal_id)
    return apply_recipe_edit(db, user, proposal_id)


def list_personal_recipes(db: Session, user: User) -> list[PersonalRecipe]:
    return (
        db.query(PersonalRecipe)
        .filter(PersonalRecipe.user_id == user.id, PersonalRecipe.is_active.is_(True))
        .order_by(PersonalRecipe.updated_at.desc())
        .all()
    )


def get_personal_recipe(db: Session, user: User, personal_recipe_id: UUID) -> PersonalRecipe:
    personal = (
        db.query(PersonalRecipe)
        .filter(PersonalRecipe.id == personal_recipe_id, PersonalRecipe.user_id == user.id)
        .first()
    )
    if not personal:
        raise HTTPException(status_code=404, detail="Personal recipe not found")
    return personal


def update_personal_recipe(
    db: Session,
    user: User,
    personal_recipe_id: UUID,
    *,
    name: Optional[str] = None,
    ingredients: Any = None,
    instructions: Any = None,
    portion_size: Optional[str] = None,
    notes: Optional[str] = None,
) -> PersonalRecipe:
    personal = get_personal_recipe(db, user, personal_recipe_id)
    if not personal.is_active:
        raise HTTPException(status_code=409, detail="Personal recipe is archived")

    now = datetime.now(timezone.utc)
    if name is not None:
        personal.name = name.strip()
    if ingredients is not None:
        personal.ingredients = _json_dumps(ingredients)
    if instructions is not None:
        personal.instructions = (
            instructions if isinstance(instructions, str) else _json_dumps(instructions)
        )
    if portion_size is not None:
        personal.portion_size = portion_size
    if notes is not None:
        personal.notes = notes

    snapshot = personal_content_snapshot(personal)
    personal.current_revision = int(personal.current_revision or 0) + 1
    personal.updated_at = now
    db.add(
        PersonalRecipeRevision(
            personal_recipe_id=personal.id,
            revision_number=personal.current_revision,
            content_snapshot=_json_dumps(snapshot),
            structured_diff=None,
            rationale="Manual edit",
            actor_user_id=user.id,
            created_at=now,
        )
    )
    db.commit()
    db.refresh(personal)
    return personal


def archive_personal_recipe(
    db: Session, user: User, personal_recipe_id: UUID
) -> PersonalRecipe:
    personal = get_personal_recipe(db, user, personal_recipe_id)
    personal.is_active = False
    personal.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(personal)
    return personal


def get_proposal(db: Session, user: User, proposal_id: UUID) -> SodieActionProposal:
    return _get_owned_proposal(db, proposal_id, user.id)


def list_thread_proposals(
    db: Session,
    user: User,
    thread_id: UUID,
    *,
    status: Optional[str] = None,
) -> list[SodieActionProposal]:
    """Owned proposals for a thread (used to reconstitute cards on resume)."""
    # Ensure the thread belongs to the caller before listing.
    thread = (
        db.query(SodieThread)
        .filter(SodieThread.id == thread_id, SodieThread.user_id == user.id)
        .first()
    )
    if not thread:
        raise HTTPException(status_code=404, detail="Sodie thread not found")

    query = db.query(SodieActionProposal).filter(
        SodieActionProposal.user_id == user.id,
        SodieActionProposal.thread_id == thread_id,
    )
    if status:
        query = query.filter(SodieActionProposal.status == status)
    return query.order_by(SodieActionProposal.created_at.asc()).all()
