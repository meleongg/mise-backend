"""Enable RLS on pantry and Sodie tables

Revision ID: c7d8e9f0
Revises: f2b8c1d4
Create Date: 2026-09-20 00:00:00.000000

Inventory (user-owned / sensitive tables):

- user_pantry_items, sodie_threads, sodie_messages — hardened in this revision
- recipe_suggestions — already has ENABLE RLS + SELECT policy (4b7c2d1a)
- weekly_plans, user_recipe_progress, users — deferred; current access model is
  FastAPI ownership checks only. Include in a follow-up once role posture is
  confirmed on the hosted database.

FastAPI role posture (expected):

- The API connects with DATABASE_URL and does not SET ROLE or populate
  request.jwt / auth.uid() on the session.
- Hosted connections typically use a table-owner or BYPASSRLS role, so ENABLE
  RLS without FORCE does not change FastAPI reads/writes.
- ENABLE RLS with no policies denies PostgREST / Data API roles that are not
  the owner and do not bypass RLS. Mise's frontend must continue to use
  FastAPI rather than querying these tables through Supabase Data API.

This migration intentionally does not create permissive policies and does not
FORCE ROW LEVEL SECURITY, so the existing FastAPI owner/bypass path keeps
working while unrestricted Data API exposure is closed for these tables.

Rollback risk:

- Downgrade disables RLS on the three tables. That restores the prior
  unrestricted Data API posture for any non-bypass role; apply only if the
  migration must be reverted and treat re-enabling RLS as required follow-up.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7d8e9f0"
down_revision: Union[str, Sequence[str], None] = "f2b8c1d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_RLS_TABLES = (
    "user_pantry_items",
    "sodie_threads",
    "sodie_messages",
)


def upgrade() -> None:
    for table in _RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    for table in reversed(_RLS_TABLES):
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
