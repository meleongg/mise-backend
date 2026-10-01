#!/usr/bin/env python3
"""
Deprecated: TheMealDB seeding has been removed.

Use scripts/seed_llm_catalog.py for Mise-owned LLM catalog recipes, then
scripts/backfill_recipe_images.py for Pexels heroes.
"""

from __future__ import annotations

import sys


def main() -> int:
    print(
        "seed_recipes.py (TheMealDB) is retired.\n"
        "Use: python scripts/seed_llm_catalog.py --per-cuisine 5\n"
        "Then: python scripts/backfill_recipe_images.py",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
