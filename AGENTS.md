# Backend contribution guide

## Working branches and draft PRs

- Keep `main` reviewable: implement each change on a dedicated branch named
  `codex/<short-description>`.
- Before opening a PR, run the relevant tests and then the full backend suite.
- Push the branch and open the PR as a draft unless the requester explicitly
  asks for a ready-for-review PR.
- Do not commit `.env`, API keys, production database URLs, or generated local
  files.

## Tests

The app creates its database and AI clients during import, so local test runs
need harmless values when a real `.env` is unavailable:

```bash
DATABASE_URL=sqlite:///:memory: OPENAI_API_KEY=test-key pytest -q
```

For a behavior change, add or update a focused regression test first, then run
the full suite. Record the exact command and result in the PR description.

## Backend conventions

- Put reusable plan and recipe business rules in `app/services/` so every
  router follows the same behavior.
- Keep HTTP validation and request orchestration in `app/routers/`.
- Prefer deterministic backend validation over relying solely on agent prompts.
