# ChefPath Backend Scripts

This directory contains scripts for managing and maintaining the ChefPath backend database and AI infrastructure.

## Available Scripts

### Hydrate Recipes (`scripts/hydrate_recipes.py`)

Fetches and stores all available recipes from TheMealDB API using exhaustive search:

```bash
python scripts/hydrate_recipes.py
```

- Populates the database with all unique recipes.
- Ensures each recipe is enriched for downstream AI use.

### Generate Embeddings (`scripts/generate_embeddings.py`)

Generates vector embeddings for all recipes missing an embedding:

```bash
python scripts/generate_embeddings.py
```

- Uses OpenAI's `text-embedding-3-small` model.
- Optimized for batch processing and cost efficiency.

### Backfill Recipe Images (`scripts/backfill_recipe_images.py`)

Fills `recipes.image_url` from Pexels for rows with no image (AI recipes, etc.):

```bash
cd backend
# .env needs DATABASE_URL + PEXELS_API_KEY
python scripts/backfill_recipe_images.py --dry-run
python scripts/backfill_recipe_images.py --limit 50
python scripts/backfill_recipe_images.py
python scripts/backfill_recipe_images.py --force --limit 20   # re-resolve bad images
```

- Default delay 18s between requests (~200/hour Pexels limit).
- Without `--force`: only rows with null/empty `image_url`.
- With `--force`: all recipes (or `--id` one); overwrites `image_url` when Pexels finds a new image, keeps the old URL if search fails.

### Clear Database (`scripts/clear_database.py`)

Removes all data while keeping table structure:

```bash
python scripts/clear_database.py
python scripts/clear_database.py --force  # Skip confirmation
```

### Testing & Evaluation

LangSmith-powered evaluation system for agent behavior and intent classification:

```bash
# Setup datasets (one-time)
python scripts/evaluate_agent.py setup

# Run evaluations
python scripts/evaluate_agent.py intent    # Intent classification only
python scripts/evaluate_agent.py agent     # Agent behavior only
python scripts/evaluate_agent.py all       # All evaluations

# View results at: https://smith.langchain.com/experiments
```
