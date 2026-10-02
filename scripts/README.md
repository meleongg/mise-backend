# Mise backend scripts

Operator utilities for the FastAPI service. Prefer `--dry-run` before mutating
hosted data.

## Catalog wipe

```bash
python scripts/wipe_catalog_linked_data.py --dry-run
python scripts/wipe_catalog_linked_data.py --force
```

Deletes recipes and dependents (plans, shopping, personal recipes, progress).
Keeps users, pantry, and Sodie threads/messages.

## Sodie chat wipe

```bash
python scripts/wipe_sodie_chats.py --dry-run
python scripts/wipe_sodie_chats.py --force
```

Deletes `sodie_messages` then `sodie_threads` (nulls proposal `thread_id` first).
Keeps users, pantry, and catalog. Use after a catalog wipe when you want a
conversational clean slate before LLM seed.

## LLM catalog seed

```bash
python scripts/seed_llm_catalog.py --dry-run
python scripts/seed_llm_catalog.py --per-cuisine 5
```

Generates Mise-owned recipes via structured LLM output (numeric `portion_size`,
embeddings). Leave images empty for Pexels backfill.

`scripts/seed_recipes.py` (legacy catalog import) is retired and exits with guidance.

## Pexels hero images

```bash
python scripts/backfill_recipe_images.py --dry-run
python scripts/backfill_recipe_images.py --limit 50
python scripts/backfill_recipe_images.py
```

Requires `PEXELS_API_KEY`. Stores `image_url` plus photographer attribution
fields when present. Default delay ~18s between calls.

## Other

- `scripts/clear_database.py` — full wipe including users (dev only; prefer
  catalog wipe for reseed).
- `scripts/evaluate_agent.py` — LangSmith evaluations when configured.
