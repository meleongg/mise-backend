"""Add prep_timeline_json snapshot on weekly_plans

Revision ID: i7d8e9f0
Revises: h6c7d8e9
Create Date: 2026-09-30 00:00:00.000000

Persists the deterministic prep timeline JSON produced at generate/swap time.
GET may fall back to live recompute when the column is null (legacy plans).

Rollback risk: downgrade drops the column; historical timeline snapshots are
removed (plans and recipes are unchanged).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "i7d8e9f0"
down_revision: Union[str, Sequence[str], None] = "h6c7d8e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "weekly_plans",
        sa.Column("prep_timeline_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("weekly_plans", "prep_timeline_json")
