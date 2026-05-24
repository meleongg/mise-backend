"""Add RLS policies for recipe_suggestions

Revision ID: 4b7c2d1a
Revises: 3d1a8f4c
Create Date: 2026-02-16 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4b7c2d1a"
down_revision: Union[str, Sequence[str], None] = "3d1a8f4c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE recipe_suggestions ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY recipe_suggestions_select_own
        ON recipe_suggestions
        FOR SELECT
        USING (user_id = auth.uid())
        """)


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS recipe_suggestions_select_own ON recipe_suggestions"
    )
    op.execute("ALTER TABLE recipe_suggestions DISABLE ROW LEVEL SECURITY")
