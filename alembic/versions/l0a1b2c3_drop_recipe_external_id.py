"""Drop unused recipes.external_id (legacy TheMealDB / import key)

Revision ID: l0a1b2c3
Revises: k9f0a1b2
Create Date: 2026-10-01 00:00:00.000000

Catalog recipes are Mise-owned (UUID primary key). external_id is no longer
written by seed or adaptive planner flows.

Rollback risk: downgrade re-adds a nullable unique external_id column (empty).
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


def downgrade() -> None:
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
