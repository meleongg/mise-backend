"""Trim legacy unused schema columns after MealDB / stub features

Revision ID: l0a1b2c3
Revises: k9f0a1b2
Create Date: 2026-10-01 00:00:00.000000

Drops:
- recipes.external_id (legacy import / TheMealDB key)
- recipes.tags (superseded by dietary_tags)
- recipes.is_ai_generated (write-only provenance flag)
- user_recipe_progress.satisfaction_rating / difficulty_rating
  (never written by live feedback API; exclusion uses feedback)
- users.sodie_memory_enabled / chat_retention_policy (UI stubs; unused)
- user_pantry_items.is_baseline (unused by shopping omit logic)

Rollback risk: re-adds empty nullable / defaulted columns; data not restored.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "l0a1b2c3"
down_revision: Union[str, Sequence[str], None] = "k9f0a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index(op.f("ix_recipes_external_id"), table_name="recipes")
    op.drop_column("recipes", "external_id")
    op.drop_column("recipes", "tags")
    op.drop_column("recipes", "is_ai_generated")

    op.drop_column("user_recipe_progress", "satisfaction_rating")
    op.drop_column("user_recipe_progress", "difficulty_rating")

    op.drop_column("users", "sodie_memory_enabled")
    op.drop_column("users", "chat_retention_policy")

    op.drop_column("user_pantry_items", "is_baseline")


def downgrade() -> None:
    op.add_column(
        "user_pantry_items",
        sa.Column(
            "is_baseline",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    op.add_column(
        "users",
        sa.Column(
            "chat_retention_policy",
            sa.String(length=20),
            nullable=False,
            server_default="18_months",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "sodie_memory_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    op.add_column(
        "user_recipe_progress",
        sa.Column("difficulty_rating", sa.Integer(), nullable=True),
    )
    op.add_column(
        "user_recipe_progress",
        sa.Column("satisfaction_rating", sa.Integer(), nullable=True),
    )

    op.add_column(
        "recipes",
        sa.Column("is_ai_generated", sa.Boolean(), nullable=True),
    )
    op.add_column("recipes", sa.Column("tags", sa.Text(), nullable=True))
    op.add_column(
        "recipes",
        sa.Column("external_id", sa.String(length=50), nullable=True),
    )
    op.create_index(
        op.f("ix_recipes_external_id"),
        "recipes",
        ["external_id"],
        unique=True,
    )
