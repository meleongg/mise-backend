#!/usr/bin/env python3
"""
Wipe catalog recipes and dependent demo data (keep users / prefs / pantry / Sodie chats).

FK-safe order matches PLAN.md catalog reseed step 1. Does not drop tables.
Does not delete users, user_pantry_items, sodie_threads, or sodie_messages.

Requires DATABASE_URL (e.g. backend/.env).

Examples:
  python scripts/wipe_catalog_linked_data.py --dry-run
  python scripts/wipe_catalog_linked_data.py --force
"""

from __future__ import annotations

import argparse
import os
import sys

script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(script_dir, os.pardir))
sys.path.insert(0, project_root)

from dotenv import load_dotenv
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import (
    PersonalRecipe,
    PersonalRecipeRevision,
    Recipe,
    RecipeSuggestion,
    RecipeVerificationRun,
    ShoppingList,
    ShoppingListItem,
    ShoppingListItemSource,
    SodieActionProposal,
    UserRecipeProgress,
    WeeklyPlan,
    WeeklyPlanEntry,
)

load_dotenv(os.path.join(project_root, ".env"))

# Child → parent within the wipe set. Users / pantry / Sodie threads kept.
WIPE_STEPS: list[tuple[str, type]] = [
    ("shopping_list_item_sources", ShoppingListItemSource),
    ("shopping_list_items", ShoppingListItem),
    ("shopping_lists", ShoppingList),
    ("weekly_plan_entries", WeeklyPlanEntry),
    ("weekly_plans", WeeklyPlan),
    ("user_recipe_progress", UserRecipeProgress),
    ("recipe_suggestions", RecipeSuggestion),
    ("recipe_verification_runs", RecipeVerificationRun),
    ("sodie_action_proposals", SodieActionProposal),
    ("personal_recipe_revisions", PersonalRecipeRevision),
    ("personal_recipes", PersonalRecipe),
    ("recipes", Recipe),
]


def table_counts(session: Session) -> dict[str, int]:
    counts: dict[str, int] = {}
    for label, model in WIPE_STEPS:
        counts[label] = int(session.scalar(select(func.count()).select_from(model)) or 0)
    return counts


def wipe_catalog_linked_data(
    session: Session, *, dry_run: bool = False, commit: bool = True
) -> dict[str, int]:
    """
    Delete catalog-linked rows in FK-safe order.

    Returns per-table counts that would be / were deleted (pre-delete counts).
    """
    before = table_counts(session)
    if dry_run:
        return before

    for _label, model in WIPE_STEPS:
        session.execute(delete(model))
    if commit:
        session.commit()
    else:
        session.flush()
    return before


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Wipe recipes and dependent plan/shopping/personal/progress data. "
            "Keeps users, pantry, and Sodie threads/messages."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print row counts only; do not delete",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip interactive confirmation (required for non-dry-run in CI)",
    )
    args = parser.parse_args()

    with SessionLocal() as session:
        counts = table_counts(session)
        total = sum(counts.values())
        print("Catalog-linked tables (will wipe):")
        for label, _model in WIPE_STEPS:
            print(f"  {label}: {counts[label]}")
        print(f"Total rows: {total}")
        print("Preserved: users, user_pantry_items, sodie_threads, sodie_messages")

        if args.dry_run:
            print("(dry-run: no deletes)")
            return 0

        if not args.force:
            response = input(
                "⚠️  Delete all catalog-linked rows above? Type 'wipe' to confirm: "
            )
            if response.strip() != "wipe":
                print("Cancelled.")
                return 0

        deleted = wipe_catalog_linked_data(session, dry_run=False)
        after = table_counts(session)
        print("Deleted:")
        for label, _model in WIPE_STEPS:
            print(f"  {label}: {deleted[label]} → {after[label]}")
        if any(after.values()):
            print("ERROR: some wipe tables still have rows", file=sys.stderr)
            return 1
        print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
