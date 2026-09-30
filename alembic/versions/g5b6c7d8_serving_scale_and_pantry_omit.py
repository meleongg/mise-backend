"""Add shopping pantry-omit columns for confirmed omit

Revision ID: g5b6c7d8
Revises: f4a5b6c7
Create Date: 2026-09-30 00:00:00.000000

Adds explicit pantry-omit flags on shopping_list_items. Baseline pantry
membership never auto-sets these; the user must confirm omit on the list.

Serving scale uses existing weekly_plan_entries.selected_servings (no new
column). Unit conversion tables remain out of scope.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "g5b6c7d8"
down_revision: Union[str, Sequence[str], None] = "f4a5b6c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "shopping_list_items",
        sa.Column(
            "omitted_by_pantry",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "shopping_list_items",
        sa.Column("pantry_omit_confirmed_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("shopping_list_items", "pantry_omit_confirmed_at")
    op.drop_column("shopping_list_items", "omitted_by_pantry")
