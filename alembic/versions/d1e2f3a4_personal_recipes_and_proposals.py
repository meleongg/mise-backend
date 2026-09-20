"""Add personal recipes and Sodie action proposals

Revision ID: d1e2f3a4
Revises: c7d8e9f0
Create Date: 2026-09-20 00:00:00.000000

Creates the first write-capable Sodie tables:

- personal_recipes / personal_recipe_revisions — user-owned lineage; catalog
  recipes remain immutable.
- sodie_action_proposals — explicit pending/applied/rejected edit proposals
  with idempotency keys and content-hash stale detection.

weekly_plan_entries is intentionally deferred; approval must not rewrite
weekly_plans.recipe_schedule until that cutover lands.

RLS posture matches c7d8e9f0: ENABLE without permissive policies and without
FORCE, so FastAPI owner/BYPASSRLS connections keep working while Data API
non-bypass roles default to deny.

Rollback risk: downgrade drops the three tables (and their RLS). Pending
proposals and personal copies would be removed; re-apply only after confirming
no user-owned personal recipes must be retained.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "d1e2f3a4"
down_revision: Union[str, Sequence[str], None] = "c7d8e9f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_RLS_TABLES = (
    "personal_recipes",
    "personal_recipe_revisions",
    "sodie_action_proposals",
)


def upgrade() -> None:
    op.create_table(
        "personal_recipes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "source_recipe_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("recipes.id"),
            nullable=True,
        ),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("ingredients", sa.Text(), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("portion_size", sa.String(length=50), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("current_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_personal_recipes_user_id", "personal_recipes", ["user_id"])
    op.create_index(
        "ix_personal_recipes_source_recipe_id", "personal_recipes", ["source_recipe_id"]
    )

    op.create_table(
        "personal_recipe_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "personal_recipe_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("personal_recipes.id"),
            nullable=False,
        ),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("content_snapshot", sa.Text(), nullable=False),
        sa.Column("structured_diff", sa.Text(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_personal_recipe_revisions_personal_recipe_id",
        "personal_recipe_revisions",
        ["personal_recipe_id"],
    )

    op.create_table(
        "sodie_action_proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "thread_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sodie_threads.id"),
            nullable=True,
        ),
        sa.Column("action_type", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column(
            "source_recipe_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("recipes.id"),
            nullable=True,
        ),
        sa.Column(
            "personal_recipe_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("personal_recipes.id"),
            nullable=True,
        ),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("diff_json", sa.Text(), nullable=False),
        sa.Column("impact_json", sa.Text(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column("source_content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("applied_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "user_id", "idempotency_key", name="uq_sodie_proposals_user_idempotency"
        ),
    )
    op.create_index(
        "ix_sodie_action_proposals_user_id", "sodie_action_proposals", ["user_id"]
    )
    op.create_index(
        "ix_sodie_action_proposals_thread_id", "sodie_action_proposals", ["thread_id"]
    )
    op.create_index(
        "ix_sodie_action_proposals_status", "sodie_action_proposals", ["status"]
    )

    for table in _RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    for table in reversed(_RLS_TABLES):
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_index("ix_sodie_action_proposals_status", table_name="sodie_action_proposals")
    op.drop_index(
        "ix_sodie_action_proposals_thread_id", table_name="sodie_action_proposals"
    )
    op.drop_index("ix_sodie_action_proposals_user_id", table_name="sodie_action_proposals")
    op.drop_table("sodie_action_proposals")

    op.drop_index(
        "ix_personal_recipe_revisions_personal_recipe_id",
        table_name="personal_recipe_revisions",
    )
    op.drop_table("personal_recipe_revisions")

    op.drop_index(
        "ix_personal_recipes_source_recipe_id", table_name="personal_recipes"
    )
    op.drop_index("ix_personal_recipes_user_id", table_name="personal_recipes")
    op.drop_table("personal_recipes")
