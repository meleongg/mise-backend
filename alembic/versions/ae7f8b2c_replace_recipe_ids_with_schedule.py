"""Replace recipe_ids with recipe_schedule in weekly_plans

Revision ID: ae7f8b2c
Revises: ac4219556a6a
Create Date: 2026-02-16 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "ae7f8b2c"
down_revision: Union[str, Sequence[str], None] = "ac4219556a6a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Migrate data from recipe_ids to recipe_schedule, then drop recipe_ids
    # First, add the new column with default value
    op.add_column(
        "weekly_plans",
        sa.Column("recipe_schedule", sa.Text(), nullable=False, server_default="[]"),
    )

    # Migrate existing data: convert recipe_ids JSON array to recipe_schedule with order
    # Preserve original array order using WITH ORDINALITY (not alphabetically sorted)
    op.execute("""
        UPDATE weekly_plans 
        SET recipe_schedule = COALESCE(
            (SELECT json_agg(
                json_build_object('recipe_id', elem, 'order', ordinality - 1)
                ORDER BY ordinality
            )
            FROM jsonb_array_elements(recipe_ids::jsonb) WITH ORDINALITY as t(elem, ordinality)),
            '[]'::json
        )
    """)

    # Drop the old column
    op.drop_column("weekly_plans", "recipe_ids")


def downgrade() -> None:
    # Reverse: recreate recipe_ids and drop recipe_schedule
    op.add_column(
        "weekly_plans",
        sa.Column("recipe_ids", sa.Text(), nullable=False),
    )

    # Migrate data back: extract recipe_ids in order from recipe_schedule
    op.execute("""
        UPDATE weekly_plans 
        SET recipe_ids = COALESCE(
            (SELECT json_agg(rec->>'recipe_id' ORDER BY (rec->>'order')::int)
            FROM jsonb_array_elements(recipe_schedule::jsonb) as rec),
            '[]'::json
        )
    """)

    op.drop_column("weekly_plans", "recipe_schedule")
