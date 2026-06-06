"""Add recipe_repeat_preference to users

Revision ID: d4e5f6a7
Revises: c8f1a2b3
Create Date: 2026-06-02 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "c8f1a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "recipe_repeat_preference",
            sa.String(length=20),
            nullable=False,
            server_default="standard",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "recipe_repeat_preference")
