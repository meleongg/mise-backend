"""Add Pexels photographer attribution columns on recipes

Revision ID: k9f0a1b2
Revises: j8e9f0a1
Create Date: 2026-09-30 00:00:00.000000

Stores photographer name and photo page URL when attaching Pexels hero images
so the UI can credit contributors (API attribution guidance).

Rollback risk: downgrade drops attribution columns; image_url is unchanged.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "k9f0a1b2"
down_revision: Union[str, Sequence[str], None] = "j8e9f0a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "recipes",
        sa.Column("image_attribution_photographer", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "recipes",
        sa.Column("image_attribution_url", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("recipes", "image_attribution_url")
    op.drop_column("recipes", "image_attribution_photographer")
