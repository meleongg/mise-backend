"""Weekly shopping list generate/refresh from plan-entry snapshots."""

from __future__ import annotations

import json
import re
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from app.models import (
    ShoppingList,
    ShoppingListItem,
    ShoppingListItemSource,
    User,
    UserPantryItem,
    WeeklyPlan,
    WeeklyPlanEntry,
)
from app.services.servings import scale_factor
from app.services.weekly_plan import ensure_plan_entries

_MEASURE_RE = re.compile(
    r"^\s*(?P<qty>\d+(?:\.\d+)?(?:/\d+)?)\s*(?P<unit>.*)$",
    re.IGNORECASE,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_name(name: str) -> str:
    return " ".join(name.lower().split())


def _parse_fraction(token: str) -> Optional[float]:
    token = token.strip()
    if not token:
        return None
    if "/" in token:
        parts = token.split("/", 1)
        try:
            num = float(parts[0])
            den = float(parts[1])
            if den == 0:
                return None
            return num / den
        except ValueError:
            return None
    try:
        return float(token)
    except ValueError:
        return None


def parse_measure(measure: str) -> tuple[Optional[float], str, bool]:
    """Return (quantity, unit, needs_review). Never invent conversions."""
    raw = (measure or "").strip()
    if not raw:
        return None, "", False
    match = _MEASURE_RE.match(raw)
    if not match:
        return None, raw.lower(), True
    qty = _parse_fraction(match.group("qty"))
    unit = (match.group("unit") or "").strip().lower()
    if qty is None:
        return None, raw.lower(), True
    return qty, unit, False


def _ingredient_rows(raw: Any) -> list[dict[str, str]]:
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            text = raw.strip()
            return [{"name": text, "measure": ""}] if text else []
    if not isinstance(raw, list):
        return []
    rows: list[dict[str, str]] = []
    for item in raw:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            measure = str(item.get("measure") or "").strip()
            if name:
                rows.append({"name": name, "measure": measure})
        elif isinstance(item, str) and item.strip():
            rows.append({"name": item.strip(), "measure": ""})
    return rows


def _display_text(name: str, quantity: Optional[float], unit: str, measure: str) -> str:
    if quantity is not None and unit:
        qty_str = (
            str(int(quantity))
            if float(quantity).is_integer()
            else f"{quantity:.2f}".rstrip("0").rstrip(".")
        )
        return f"{qty_str} {unit} {name}".strip()
    if measure:
        return f"{measure} {name}".strip()
    return name


def _aggregate_key(normalized_name: str, unit: str) -> str:
    return f"{normalized_name}::{unit}"


def _format_qty(quantity: float) -> str:
    if float(quantity).is_integer():
        return str(int(quantity))
    return f"{quantity:.2f}".rstrip("0").rstrip(".")


def build_aggregated_items(
    entries: list[WeeklyPlanEntry],
) -> list[dict[str, Any]]:
    """
    Aggregate ingredients across included plan entries.

    Same normalized name + unit with parseable quantities are summed.
    Differing units for the same name stay separate with needs_review.
    When selected_servings and snapshot portion_size both parse, quantities
    are scaled (no unit conversion).
    """
    buckets: dict[str, dict[str, Any]] = {}
    name_units: dict[str, set[str]] = defaultdict(set)

    for entry in entries:
        if (entry.lifecycle_state or "planned") == "omitted":
            continue
        try:
            snapshot = (
                json.loads(entry.recipe_snapshot) if entry.recipe_snapshot else {}
            )
        except json.JSONDecodeError:
            snapshot = {}
        recipe_name = str(snapshot.get("name") or "Recipe")
        baseline = snapshot.get("portion_size")
        factor, servings_review, servings_reason = scale_factor(
            entry.selected_servings, baseline
        )
        for row in _ingredient_rows(snapshot.get("ingredients")):
            name = row["name"]
            measure = row["measure"]
            normalized = _normalize_name(name)
            quantity, unit, ambiguous = parse_measure(measure)
            source_amount = measure or None
            review = ambiguous or servings_review
            reason = None
            confidence = "high"
            if ambiguous:
                confidence = "low"
                reason = "ambiguous measure"
            if servings_review:
                confidence = "low"
                reason = servings_reason
            if quantity is not None and factor != 1.0:
                quantity = float(quantity) * float(factor)
                if unit:
                    source_amount = f"{_format_qty(quantity)} {unit}".strip()
                else:
                    source_amount = _format_qty(quantity)
            elif factor != 1.0 and quantity is None and measure:
                review = True
                confidence = "low"
                reason = "servings scale skipped; unparseable measure quantity"

            key = _aggregate_key(normalized, unit)
            name_units[normalized].add(unit)
            bucket = buckets.get(key)
            if bucket is None:
                buckets[key] = {
                    "normalized_name": normalized,
                    "display_name": name,
                    "quantity": quantity,
                    "unit": unit or None,
                    "needs_review": review,
                    "confidence": confidence,
                    "reason": reason,
                    "sources": [
                        {
                            "weekly_plan_entry_id": entry.id,
                            "source_amount": source_amount,
                            "recipe_name": recipe_name,
                        }
                    ],
                    "raw_measures": [source_amount] if source_amount else [],
                }
            else:
                bucket["sources"].append(
                    {
                        "weekly_plan_entry_id": entry.id,
                        "source_amount": source_amount,
                        "recipe_name": recipe_name,
                    }
                )
                if source_amount:
                    bucket["raw_measures"].append(source_amount)
                if quantity is not None and bucket["quantity"] is not None:
                    bucket["quantity"] = float(bucket["quantity"]) + float(quantity)
                elif quantity is not None and bucket["quantity"] is None:
                    bucket["quantity"] = quantity
                    bucket["needs_review"] = True
                    bucket["confidence"] = "medium"
                    bucket["reason"] = "mixed parseable and unparseable measures"
                elif quantity is None and bucket["quantity"] is not None:
                    bucket["needs_review"] = True
                    bucket["confidence"] = "medium"
                    bucket["reason"] = "mixed parseable and unparseable measures"
                if review:
                    bucket["needs_review"] = True
                    bucket["confidence"] = "low"
                    if reason:
                        bucket["reason"] = reason

    # Mark same ingredient with multiple units for review (never convert).
    for bucket in buckets.values():
        units = name_units.get(bucket["normalized_name"], set())
        if len(units) > 1:
            bucket["needs_review"] = True
            bucket["confidence"] = "low"
            bucket["reason"] = "same ingredient with differing units; review before combining"

    items: list[dict[str, Any]] = []
    for idx, bucket in enumerate(
        sorted(buckets.values(), key=lambda b: b["normalized_name"])
    ):
        measure_fallback = ", ".join(
            m for m in bucket["raw_measures"] if m
        ) or ""
        items.append(
            {
                "normalized_name": bucket["normalized_name"],
                "display_text": _display_text(
                    bucket["display_name"],
                    bucket["quantity"],
                    bucket["unit"] or "",
                    measure_fallback if bucket["quantity"] is None else "",
                ),
                "quantity": bucket["quantity"],
                "unit": bucket["unit"],
                "needs_review": bucket["needs_review"],
                "confidence": bucket["confidence"],
                "reason": bucket["reason"],
                "sort_order": idx,
                "sources": bucket["sources"],
            }
        )
    return items


def serialize_shopping_list(
    shopping_list: ShoppingList,
    *,
    pantry_names: Optional[set[str]] = None,
) -> dict[str, Any]:
    pantry = pantry_names or set()
    items_out = []
    for item in shopping_list.items or []:
        items_out.append(
            {
                "id": item.id,
                "shopping_list_id": item.shopping_list_id,
                "normalized_name": item.normalized_name,
                "display_text": item.display_text,
                "quantity": item.quantity,
                "unit": item.unit,
                "aisle": item.aisle,
                "is_checked": item.is_checked,
                "is_user_edit": item.is_user_edit,
                "needs_review": item.needs_review,
                "omitted_by_pantry": bool(item.omitted_by_pantry),
                "pantry_omit_confirmed_at": item.pantry_omit_confirmed_at,
                "pantry_match": item.normalized_name in pantry,
                "confidence": item.confidence,
                "reason": item.reason,
                "sort_order": item.sort_order,
                "sources": [
                    {
                        "id": src.id,
                        "shopping_list_item_id": src.shopping_list_item_id,
                        "weekly_plan_entry_id": src.weekly_plan_entry_id,
                        "source_amount": src.source_amount,
                        "inclusion_state": src.inclusion_state,
                    }
                    for src in (item.sources or [])
                ],
                "created_at": item.created_at,
                "updated_at": item.updated_at,
            }
        )
    return {
        "id": shopping_list.id,
        "user_id": shopping_list.user_id,
        "weekly_plan_id": shopping_list.weekly_plan_id,
        "title": shopping_list.title,
        "status": shopping_list.status,
        "retailer_snapshot": shopping_list.retailer_snapshot,
        "location_snapshot": shopping_list.location_snapshot,
        "created_at": shopping_list.created_at,
        "updated_at": shopping_list.updated_at,
        "archived_at": shopping_list.archived_at,
        "items": items_out,
    }


def pantry_normalized_names(db: Session, user_id: uuid.UUID) -> set[str]:
    rows = (
        db.query(UserPantryItem.name)
        .filter(UserPantryItem.user_id == user_id)
        .all()
    )
    return {_normalize_name(name) for (name,) in rows if name}


def _load_list(db: Session, list_id: uuid.UUID, user_id: uuid.UUID) -> ShoppingList:
    shopping_list = (
        db.query(ShoppingList)
        .options(
            joinedload(ShoppingList.items).joinedload(ShoppingListItem.sources)
        )
        .filter(ShoppingList.id == list_id, ShoppingList.user_id == user_id)
        .first()
    )
    if not shopping_list:
        raise HTTPException(status_code=404, detail="Shopping list not found")
    return shopping_list


def get_active_shopping_list(
    db: Session, user: User, week_number: Optional[int] = None
) -> Optional[ShoppingList]:
    query = db.query(ShoppingList).options(
        joinedload(ShoppingList.items).joinedload(ShoppingListItem.sources)
    ).filter(
        ShoppingList.user_id == user.id,
        ShoppingList.status == "active",
    )
    if week_number is not None:
        plan = (
            db.query(WeeklyPlan)
            .filter(
                WeeklyPlan.user_id == user.id,
                WeeklyPlan.week_number == week_number,
            )
            .first()
        )
        if not plan:
            return None
        query = query.filter(ShoppingList.weekly_plan_id == plan.id)
    shopping_list = query.order_by(ShoppingList.updated_at.desc()).first()
    return shopping_list


def generate_or_refresh_shopping_list(
    db: Session, user: User, week_number: int
) -> ShoppingList:
    plan = (
        db.query(WeeklyPlan)
        .filter(
            WeeklyPlan.user_id == user.id,
            WeeklyPlan.week_number == week_number,
        )
        .first()
    )
    if not plan:
        raise HTTPException(status_code=404, detail="Weekly plan not found")

    ensure_plan_entries(plan, db)
    entries = (
        db.query(WeeklyPlanEntry)
        .filter(WeeklyPlanEntry.weekly_plan_id == plan.id)
        .order_by(WeeklyPlanEntry.position.asc())
        .all()
    )
    if not entries:
        raise HTTPException(
            status_code=400, detail="Weekly plan has no entries to shop from"
        )

    aggregated = build_aggregated_items(entries)

    existing = (
        db.query(ShoppingList)
        .options(
            joinedload(ShoppingList.items).joinedload(ShoppingListItem.sources)
        )
        .filter(
            ShoppingList.user_id == user.id,
            ShoppingList.weekly_plan_id == plan.id,
            ShoppingList.status == "active",
        )
        .first()
    )

    preserved: dict[str, dict[str, Any]] = {}
    if existing:
        for item in existing.items or []:
            preserved[item.normalized_name] = {
                "is_checked": bool(item.is_checked),
                "is_user_edit": bool(item.is_user_edit),
                "display_text": item.display_text,
                "quantity": item.quantity,
                "unit": item.unit,
                "aisle": item.aisle,
                "omitted_by_pantry": bool(item.omitted_by_pantry),
                "pantry_omit_confirmed_at": item.pantry_omit_confirmed_at,
            }
        for item in list(existing.items or []):
            db.delete(item)
        db.flush()
        shopping_list = existing
        shopping_list.title = f"Week {week_number} shopping"
        shopping_list.retailer_snapshot = user.preferred_retailer
        shopping_list.location_snapshot = user.city
        shopping_list.updated_at = _now()
    else:
        for other in (
            db.query(ShoppingList)
            .filter(
                ShoppingList.user_id == user.id,
                ShoppingList.status == "active",
            )
            .all()
        ):
            other.status = "archived"
            other.archived_at = _now()
            other.updated_at = _now()

        shopping_list = ShoppingList(
            id=uuid.uuid4(),
            user_id=user.id,
            weekly_plan_id=plan.id,
            title=f"Week {week_number} shopping",
            status="active",
            retailer_snapshot=user.preferred_retailer,
            location_snapshot=user.city,
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(shopping_list)
        db.flush()

    seen_names: set[str] = set()
    for row in aggregated:
        prior = preserved.get(row["normalized_name"])
        seen_names.add(row["normalized_name"])
        item = ShoppingListItem(
            id=uuid.uuid4(),
            shopping_list_id=shopping_list.id,
            normalized_name=row["normalized_name"],
            display_text=(
                prior["display_text"]
                if prior and prior["is_user_edit"]
                else row["display_text"]
            ),
            quantity=(
                prior["quantity"] if prior and prior["is_user_edit"] else row["quantity"]
            ),
            unit=prior["unit"] if prior and prior["is_user_edit"] else row["unit"],
            aisle=prior["aisle"] if prior else None,
            is_checked=bool(prior["is_checked"]) if prior else False,
            is_user_edit=bool(prior["is_user_edit"]) if prior else False,
            omitted_by_pantry=bool(prior["omitted_by_pantry"]) if prior else False,
            pantry_omit_confirmed_at=(
                prior["pantry_omit_confirmed_at"] if prior else None
            ),
            needs_review=row["needs_review"],
            confidence=row["confidence"],
            reason=row["reason"],
            sort_order=row["sort_order"],
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(item)
        db.flush()
        for src in row["sources"]:
            db.add(
                ShoppingListItemSource(
                    id=uuid.uuid4(),
                    shopping_list_item_id=item.id,
                    weekly_plan_entry_id=src["weekly_plan_entry_id"],
                    source_amount=src["source_amount"],
                    inclusion_state="included",
                    created_at=_now(),
                )
            )

    # Keep custom user-edit rows that are no longer in the plan aggregate.
    extra_order = len(aggregated)
    for name, prior in preserved.items():
        if name in seen_names or not prior["is_user_edit"]:
            continue
        db.add(
            ShoppingListItem(
                id=uuid.uuid4(),
                shopping_list_id=shopping_list.id,
                normalized_name=name,
                display_text=prior["display_text"],
                quantity=prior["quantity"],
                unit=prior["unit"],
                aisle=prior["aisle"],
                is_checked=prior["is_checked"],
                is_user_edit=True,
                omitted_by_pantry=bool(prior.get("omitted_by_pantry")),
                pantry_omit_confirmed_at=prior.get("pantry_omit_confirmed_at"),
                needs_review=False,
                confidence="high",
                reason="user-added",
                sort_order=extra_order,
                created_at=_now(),
                updated_at=_now(),
            )
        )
        extra_order += 1

    db.commit()
    return _load_list(db, shopping_list.id, user.id)


def update_shopping_list_item(
    db: Session,
    user: User,
    item_id: uuid.UUID,
    *,
    is_checked: Optional[bool] = None,
    display_text: Optional[str] = None,
    quantity: Optional[float] = None,
    unit: Optional[str] = None,
    omitted_by_pantry: Optional[bool] = None,
    confirm_pantry_omit: Optional[bool] = None,
) -> ShoppingList:
    item = (
        db.query(ShoppingListItem)
        .join(ShoppingList)
        .filter(
            ShoppingListItem.id == item_id,
            ShoppingList.user_id == user.id,
        )
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="Shopping list item not found")

    if is_checked is not None:
        item.is_checked = is_checked
    if display_text is not None:
        item.display_text = display_text.strip()
        item.is_user_edit = True
    if quantity is not None:
        item.quantity = quantity
        item.is_user_edit = True
    if unit is not None:
        item.unit = unit.strip() or None
        item.is_user_edit = True
    if omitted_by_pantry is not None:
        if omitted_by_pantry and not confirm_pantry_omit:
            raise HTTPException(
                status_code=400,
                detail="confirm_pantry_omit must be true to omit an item via pantry",
            )
        item.omitted_by_pantry = omitted_by_pantry
        item.pantry_omit_confirmed_at = _now() if omitted_by_pantry else None
    item.updated_at = _now()
    item.shopping_list.updated_at = _now()
    db.commit()
    return _load_list(db, item.shopping_list_id, user.id)


def sync_shopping_check_states(
    db: Session,
    user: User,
    updates: list[dict[str, Any]],
) -> ShoppingList:
    """Apply queued check toggles; last client_updated_at per item_id wins.

    Unknown or unauthorized item ids are skipped (offline queues may be stale).
    """
    if not updates:
        raise HTTPException(status_code=422, detail="No check updates provided")

    latest: dict[uuid.UUID, dict[str, Any]] = {}
    for raw in updates:
        item_id = raw.get("item_id")
        if item_id is None:
            continue
        if not isinstance(item_id, uuid.UUID):
            try:
                item_id = uuid.UUID(str(item_id))
            except (TypeError, ValueError):
                continue
        client_at = raw.get("client_updated_at")
        if client_at is None:
            continue
        if getattr(client_at, "tzinfo", None) is None:
            client_at = client_at.replace(tzinfo=timezone.utc)
        prev = latest.get(item_id)
        if prev is None or client_at >= prev["client_updated_at"]:
            latest[item_id] = {
                "item_id": item_id,
                "is_checked": bool(raw.get("is_checked")),
                "client_updated_at": client_at,
            }

    if not latest:
        raise HTTPException(status_code=422, detail="No valid check updates")

    item_ids = list(latest.keys())
    rows = (
        db.query(ShoppingListItem)
        .join(ShoppingList)
        .filter(
            ShoppingListItem.id.in_(item_ids),
            ShoppingList.user_id == user.id,
        )
        .all()
    )
    if not rows:
        raise HTTPException(status_code=404, detail="No matching shopping list items")

    list_ids = {row.shopping_list_id for row in rows}
    if len(list_ids) != 1:
        raise HTTPException(
            status_code=422,
            detail="Check sync updates must belong to one shopping list",
        )

    by_id = {row.id: row for row in rows}
    touched_list_id = next(iter(list_ids))
    now = _now()
    for item_id, payload in latest.items():
        item = by_id.get(item_id)
        if item is None:
            continue
        item.is_checked = payload["is_checked"]
        client_at = payload["client_updated_at"]
        # Clamp far-future client clocks to server now (±5 minutes skew OK).
        if client_at.timestamp() - now.timestamp() > 300:
            item.updated_at = now
        else:
            item.updated_at = client_at
    shopping_list = rows[0].shopping_list
    shopping_list.updated_at = now
    db.commit()
    return _load_list(db, touched_list_id, user.id)


def update_plan_entry_servings(
    db: Session, user: User, entry_id: uuid.UUID, selected_servings: Optional[float]
) -> WeeklyPlanEntry:
    entry = (
        db.query(WeeklyPlanEntry)
        .join(WeeklyPlan, WeeklyPlanEntry.weekly_plan_id == WeeklyPlan.id)
        .filter(
            WeeklyPlanEntry.id == entry_id,
            WeeklyPlan.user_id == user.id,
        )
        .first()
    )
    if not entry:
        raise HTTPException(status_code=404, detail="Plan entry not found")
    if selected_servings is not None and selected_servings <= 0:
        raise HTTPException(status_code=422, detail="selected_servings must be > 0")
    entry.selected_servings = selected_servings
    entry.updated_at = _now()
    db.commit()
    db.refresh(entry)
    return entry
