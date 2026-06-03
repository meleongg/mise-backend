#!/usr/bin/env python3
"""
Backfill recipes.image_url from Pexels for rows missing a hero image.

Requires DATABASE_URL and PEXELS_API_KEY in the environment (e.g. backend/.env).

Examples:
  python scripts/backfill_recipe_images.py --dry-run
  python scripts/backfill_recipe_images.py --limit 50
  python scripts/backfill_recipe_images.py --id <recipe-uuid>
  python scripts/backfill_recipe_images.py --force --limit 20
  python scripts/backfill_recipe_images.py
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import uuid

script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(script_dir, os.pardir))
sys.path.insert(0, project_root)

from dotenv import load_dotenv
from sqlalchemy import or_, select

from app.database import SessionLocal
from app.models import Recipe
from app.services.recipe_image import attach_recipe_image, pexels_enabled

load_dotenv(os.path.join(project_root, ".env"))


def recipes_for_backfill(
    session,
    recipe_id: uuid.UUID | None,
    limit: int | None,
    *,
    force: bool,
):
    stmt = select(Recipe)
    if not force:
        stmt = stmt.where(or_(Recipe.image_url.is_(None), Recipe.image_url == ""))
    if recipe_id:
        stmt = stmt.where(Recipe.id == recipe_id)
    stmt = stmt.order_by(Recipe.created_at.asc())
    if limit:
        stmt = stmt.limit(limit)
    return list(session.scalars(stmt).all())


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill recipe images via Pexels")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print candidates only; do not call Pexels or update rows",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-resolve images even when image_url is already set (overwrites on success)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max recipes to process",
    )
    parser.add_argument(
        "--id",
        type=str,
        default=None,
        help="Process a single recipe UUID",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=18.0,
        help="Seconds between API calls (default ~200/hour)",
    )
    args = parser.parse_args()

    recipe_id = uuid.UUID(args.id) if args.id else None

    with SessionLocal() as session:
        candidates = recipes_for_backfill(
            session, recipe_id, args.limit, force=args.force
        )

    label = "Recipes to process" if args.force else "Recipes missing image_url"
    print(f"{label}: {len(candidates)}")
    if args.force:
        print(
            "(force: existing image_url will be replaced when Pexels returns a match)"
        )
    for recipe in candidates[:10]:
        url_hint = ""
        if recipe.image_url:
            short = (
                recipe.image_url
                if len(recipe.image_url) <= 50
                else recipe.image_url[:50] + "..."
            )
            url_hint = f" | {short}"
        print(f"  - {recipe.id} | {recipe.name} | {recipe.cuisine}{url_hint}")
    if len(candidates) > 10:
        print(f"  ... and {len(candidates) - 10} more")

    if args.dry_run:
        return 0

    if not pexels_enabled():
        print("PEXELS_API_KEY not set or PEXELS_API_ENABLED=false. Aborting.")
        return 1

    updated = 0
    failed = 0
    unchanged = 0

    for i, recipe in enumerate(candidates):
        with SessionLocal() as session:
            db_recipe = session.get(Recipe, recipe.id)
            if not db_recipe:
                continue

            prior_url = db_recipe.image_url
            print(f"[{i + 1}/{len(candidates)}] {db_recipe.name}...")
            url = attach_recipe_image(db_recipe, session, force=args.force)
            if url and url != prior_url:
                updated += 1
                print(f"  -> {url}")
            elif url:
                unchanged += 1
                print(f"  -> unchanged ({url[:60]}...)")
            else:
                failed += 1
                print(
                    "  -> no image found (existing URL kept)"
                    if prior_url
                    else "  -> no image found"
                )

        if i < len(candidates) - 1 and args.delay > 0:
            time.sleep(args.delay)

    print(f"Done. Updated: {updated}, unchanged: {unchanged}, no result: {failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
