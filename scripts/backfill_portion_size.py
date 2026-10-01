#!/usr/bin/env python3
"""
Normalize catalog and plan-entry portion / servings strings for shopping scale.

Rewrites ranges and open-ended labels to the low-end canonical form
("N servings"). Soft forms like "Serves 4" / "4 people" become "4 servings".
Live parse_servings remains strict (no midpoints).

Requires DATABASE_URL in the environment (e.g. backend/.env).

Examples:
  python scripts/backfill_portion_size.py --dry-run
  python scripts/backfill_portion_size.py --limit 50
  python scripts/backfill_portion_size.py --recipes-only
  python scripts/backfill_portion_size.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid

script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(script_dir, os.pardir))
sys.path.insert(0, project_root)

from dotenv import load_dotenv
from sqlalchemy import select

from app.database import SessionLocal
from app.models import PersonalRecipe, Recipe, WeeklyPlanEntry
from app.services.servings import normalize_portion_size_for_backfill

load_dotenv(os.path.join(project_root, ".env"))


def _rewrite(value: str | None) -> str | None:
    return normalize_portion_size_for_backfill(value)


def _snapshot_dict(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def backfill_recipes(session, *, limit: int | None, dry_run: bool) -> int:
    stmt = select(Recipe).order_by(Recipe.created_at.asc())
    if limit:
        stmt = stmt.limit(limit)
    rows = list(session.scalars(stmt).all())
    updated = 0
    for recipe in rows:
        nxt = _rewrite(recipe.portion_size)
        if not nxt:
            continue
        print(f"  recipe {recipe.id} | {recipe.name!r}: {recipe.portion_size!r} → {nxt!r}")
        if not dry_run:
            recipe.portion_size = nxt
        updated += 1
    return updated


def backfill_personal(session, *, limit: int | None, dry_run: bool) -> int:
    stmt = select(PersonalRecipe).order_by(PersonalRecipe.created_at.asc())
    if limit:
        stmt = stmt.limit(limit)
    rows = list(session.scalars(stmt).all())
    updated = 0
    for personal in rows:
        nxt = _rewrite(personal.portion_size)
        if not nxt:
            continue
        print(
            f"  personal {personal.id} | {personal.name!r}: "
            f"{personal.portion_size!r} → {nxt!r}"
        )
        if not dry_run:
            personal.portion_size = nxt
        updated += 1
    return updated


def backfill_plan_entries(session, *, limit: int | None, dry_run: bool) -> int:
    stmt = select(WeeklyPlanEntry).order_by(WeeklyPlanEntry.created_at.asc())
    if limit:
        stmt = stmt.limit(limit)
    rows = list(session.scalars(stmt).all())
    updated = 0
    for entry in rows:
        changed = False
        snap = _snapshot_dict(entry.recipe_snapshot)
        snap_portion = snap.get("portion_size")
        if isinstance(snap_portion, str):
            nxt_snap = _rewrite(snap_portion)
            if nxt_snap:
                print(
                    f"  entry {entry.id} snapshot.portion_size: "
                    f"{snap_portion!r} → {nxt_snap!r}"
                )
                snap["portion_size"] = nxt_snap
                if not dry_run:
                    entry.recipe_snapshot = json.dumps(snap)
                changed = True

        nxt_sel = _rewrite(entry.selected_servings)
        if nxt_sel:
            print(
                f"  entry {entry.id} selected_servings: "
                f"{entry.selected_servings!r} → {nxt_sel!r}"
            )
            if not dry_run:
                entry.selected_servings = nxt_sel
            changed = True

        if changed:
            updated += 1
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill portion_size / selected_servings to parseable form"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print rewrites only; do not commit",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max rows per table to scan",
    )
    parser.add_argument(
        "--recipes-only",
        action="store_true",
        help="Only rewrite catalog Recipe.portion_size",
    )
    parser.add_argument(
        "--id",
        type=str,
        default=None,
        help="Process a single catalog recipe UUID (recipes table only)",
    )
    args = parser.parse_args()

    with SessionLocal() as session:
        total = 0
        if args.id:
            recipe_id = uuid.UUID(args.id)
            recipe = session.get(Recipe, recipe_id)
            if recipe is None:
                print(f"Recipe not found: {args.id}")
                return 1
            nxt = _rewrite(recipe.portion_size)
            if not nxt:
                print(f"No rewrite for {recipe.id}: {recipe.portion_size!r}")
                return 0
            print(f"  recipe {recipe.id} | {recipe.name!r}: {recipe.portion_size!r} → {nxt!r}")
            if not args.dry_run:
                recipe.portion_size = nxt
                session.commit()
            return 0

        print("Catalog recipes:")
        total += backfill_recipes(session, limit=args.limit, dry_run=args.dry_run)

        if not args.recipes_only:
            print("Personal recipes:")
            total += backfill_personal(session, limit=args.limit, dry_run=args.dry_run)
            print("Plan entries (snapshot + selected_servings):")
            total += backfill_plan_entries(
                session, limit=args.limit, dry_run=args.dry_run
            )

        print(f"Rows with rewrites: {total}")
        if args.dry_run:
            print("(dry-run: no commits)")
            return 0
        session.commit()
        print("Committed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
