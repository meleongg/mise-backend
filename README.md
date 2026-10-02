# Mise backend

FastAPI service for [Mise](https://cookwithmise.vercel.app) — adaptive weekly
meal planning, shopping lists, Kitchen Mode context, and Sodie (AI coaching and
proposals) consumed by the Next.js client.

## Product surfaces (API)

| Area | Notes |
| --- | --- |
| Auth / account | JWT register/login/refresh; account update and delete |
| Preferences / pantry | Cooking prefs + pantry items for planning and shopping omit |
| Weekly plans | Verified generation, entries as membership SoT, swaps, servings scale, prep timeline |
| Shopping | Lists from plan entries; check toggles; offline-friendly batch sync |
| Recipes / progress | Catalog + personal recipes; cook feedback; week-aware entry overlays |
| Sodie | Page-scoped threads/messages/proposals (plan, recipe, kitchen, settings, personal, analytics) |
| Operator scripts | Catalog wipe, LLM catalog seed, Pexels image backfill, Sodie chat wipe — see `scripts/README.md` |

## Architecture

- **API and persistence:** FastAPI, Pydantic, SQLAlchemy, Alembic, PostgreSQL
- **Planning:** LangGraph retrieval (pgvector + preference filters), deterministic
  verification gate, optional evaluator / one-shot repair
- **Plan integrity:** `weekly_plan_entries` sole membership/order; progress and
  cooldown exclusions keep recommendations consistent
- **Reliability:** JWT auth, explicit CORS origins, per-IP and per-user AI rate
  limits, moderation, Railway health checks, pytest coverage

## Run locally

```bash
cd mise-backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn main:app --reload
```

### Required / common env

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | PostgreSQL (production) or SQLite (light local/tests) |
| `SECRET_KEY` | JWT signing; set a long random value in production |
| `CORS_ORIGINS` | Exact frontend origin(s) outside local dev |
| `OPENAI_API_KEY` | Plan generation, Sodie, embeddings, moderation |
| `PEXELS_API_KEY` | Optional; recipe hero image backfill |
| `LANGCHAIN_API_KEY` / `LANGSMITH_TRACING` | Optional LangSmith tracing |

See `.env.example` for rate-limit and moderation knobs. Adaptive planning needs
PostgreSQL with `pgvector` and `OPENAI_API_KEY`; SQLite is only for lightweight
dev and unit tests.

### Migrations

```bash
alembic upgrade head
```

Apply on hosted after merging migrations before relying on new columns/tables.

## Test

```bash
DATABASE_URL=sqlite:///:memory: OPENAI_API_KEY=test-key pytest -q
```

## Deployment

Railway runs `uvicorn main:app` and verifies `/health` (includes DB
connectivity). Configure production secrets and database URLs in the deployment
environment; never commit them.

## Related

Frontend client: `mise-frontend`. Operator utilities: `scripts/README.md`.
