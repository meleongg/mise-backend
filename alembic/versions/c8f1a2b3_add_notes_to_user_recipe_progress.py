"""Add notes to user_recipe_progress

Revision ID: c8f1a2b3
Revises: 4b7c2d1a
Create Date: 2026-05-23 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c8f1a2b3"
down_revision: Union[str, Sequence[str], None] = "4b7c2d1a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_recipe_progress",
        sa.Column("notes", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_recipe_progress", "notes")
