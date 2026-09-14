# Backend contribution guide

## System architecture and constraints

- **Primary language/framework:** Python, FastAPI, SQLAlchemy, Alembic, and
  PostgreSQL.
- **AI workflow:** OpenAI through LangChain/LangGraph. Keep deterministic
  authorization, validation, and business rules outside prompts.
- **Architecture:** Put reusable plan, recipe, and agent rules in
  `app/services/`; keep HTTP validation and orchestration in `app/routers/`.
- **Data safety:** Do not commit `.env`, API keys, production URLs, or generated
  local files. Treat user data and agent context as untrusted.

## Execution and verification

The app creates its database and AI clients during import. When a real `.env`
is unavailable, run tests with harmless values:

```bash
DATABASE_URL=sqlite:///:memory: OPENAI_API_KEY=test-key pytest -q
```

- For behavior changes, add or update a focused regression test first, then run
  the complete backend suite with the command above.
- For migrations, test upgrade/backfill behavior and record rollback risk.
- Run `git diff --check` before handoff.

## Core agent boundaries

- **Dependency guard:** Do not add packages for trivial tasks. Prefer standard
  library or existing dependencies; request approval before adding a dependency.
- **Architectural isolation:** Do not mix business rules into routers or model
  calls. Keep database mutation paths transactional and idempotent.
- **AI safety:** An agent proposal must be server-validated and explicitly
  approved before it writes user-facing data.
- **Git hygiene:** Never commit directly to the default branch. Use a clean,
  short-lived `feat/<description>` or `fix/<description>` branch.

## Pull-request workflow

- Use [.github/PULL_REQUEST_TEMPLATE.md](.github/PULL_REQUEST_TEMPLATE.md).
- Recommend exactly one review tier based on the highest-risk change:
  **Auto-approve** (formatting or standard documentation), **Spot-check**
  (isolated low-risk UI or mechanical work), or **Full review** (architecture,
  business logic, auth, data, APIs, AI, dependencies, or migrations).
- A review-tier recommendation never authorizes merging. Only the user may
  approve, mark ready, or merge a pull request.
- For substantial feature work, commit validated changes, push the branch, and
  open a draft PR. Small changes and experiments do not require a new draft.
- Provide clickable links to available deliverables in handoffs. Include visual
  evidence for user-visible work when reliable capture is available; otherwise
  state why it is unavailable.

## Definition of done

Before presenting substantial work as complete or opening a draft PR:

1. Run focused tests, then the full backend suite.
2. For migrations, verify upgrade/backfill behavior.
3. Run `git diff --check` and explain the changed files and risks.
4. Use the `explain-diff-html` skill for substantial or high-risk changes;
   provide a clean terminal diff explanation for small follow-ups.
5. Commit, push, and open the required draft PR; do not mark it ready, approve,
   or merge it.
