"""Add weekly_plan_entries for plan-entry lineage

Revision ID: e3f4a5b6
Revises: d1e2f3a4
Create Date: 2026-09-27 00:00:00.000000

Introduces normalized weekly plan slots that can reference a catalog recipe
or a personal recipe, with an immutable recipe snapshot at bind time.

`weekly_plans.recipe_schedule` remains the compatibility source of truth for
this slice; application code dual-writes entries and lazily backfills on read.

RLS posture matches prior tables: ENABLE without permissive policies and
without FORCE.

Rollback risk: downgrade drops `weekly_plan_entries` (and its RLS). Re-apply
only after confirming dual-write consumers can fall back to recipe_schedule.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "e3f4a5b6"
down_revision: Union[str, Sequence[str], None] = "d1e2f3a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "weekly_plan_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "weekly_plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("weekly_plans.id"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "catalog_recipe_id",
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
        sa.Column("recipe_snapshot", sa.Text(), nullable=False),
        sa.Column("selected_servings", sa.String(length=50), nullable=True),
        sa.Column(
            "lifecycle_state",
            sa.String(length=20),
            nullable=False,
            server_default="planned",
        ),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_weekly_plan_entries_weekly_plan_id",
        "weekly_plan_entries",
        ["weekly_plan_id"],
    )
    op.create_index(
        "ix_weekly_plan_entries_catalog_recipe_id",
        "weekly_plan_entries",
        ["catalog_recipe_id"],
    )
    op.create_index(
        "ix_weekly_plan_entries_personal_recipe_id",
        "weekly_plan_entries",
        ["personal_recipe_id"],
    )
    op.create_index(
        "ix_weekly_plan_entries_plan_position",
        "weekly_plan_entries",
        ["weekly_plan_id", "position"],
        unique=True,
    )
    op.execute("ALTER TABLE weekly_plan_entries ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("ALTER TABLE weekly_plan_entries DISABLE ROW LEVEL SECURITY")
    op.drop_index(
        "ix_weekly_plan_entries_plan_position", table_name="weekly_plan_entries"
    )
    op.drop_index(
        "ix_weekly_plan_entries_personal_recipe_id", table_name="weekly_plan_entries"
    )
    op.drop_index(
        "ix_weekly_plan_entries_catalog_recipe_id", table_name="weekly_plan_entries"
    )
    op.drop_index(
        "ix_weekly_plan_entries_weekly_plan_id", table_name="weekly_plan_entries"
    )
    op.drop_table("weekly_plan_entries")
