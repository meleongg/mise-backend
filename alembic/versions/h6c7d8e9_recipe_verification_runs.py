"""Add recipe_verification_runs for plan generate gate audit

Revision ID: h6c7d8e9
Revises: g5b6c7d8
Create Date: 2026-09-30 00:00:00.000000

Persists deterministic gate outcomes for weekly plan generation. Candidate
IDs and per-failure recipe_id details are stored for audit only; client APIs
must expose failure codes and display messages without leaking candidates.

RLS posture matches d1e2f3a4: ENABLE without permissive policies and without
FORCE.

Rollback risk: downgrade drops recipe_verification_runs (and RLS). Historical
generate audit rows are removed.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "h6c7d8e9"
down_revision: Union[str, Sequence[str], None] = "g5b6c7d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_RLS_TABLES = ("recipe_verification_runs",)


def upgrade() -> None:
    op.create_table(
        "recipe_verification_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("target_week_number", sa.Integer(), nullable=False),
        sa.Column("flow", sa.String(length=20), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("final_status", sa.String(length=20), nullable=False),
        sa.Column("deterministic_passed", sa.Boolean(), nullable=False),
        sa.Column("failure_codes_json", sa.Text(), nullable=False),
        sa.Column("failures_detail_json", sa.Text(), nullable=True),
        sa.Column("candidate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("candidate_recipe_ids_json", sa.Text(), nullable=True),
        sa.Column("search_attempts", sa.Integer(), nullable=True),
        sa.Column("generation_attempts", sa.Integer(), nullable=True),
        sa.Column("evaluator_kind", sa.String(length=40), nullable=False),
        sa.Column("evaluator_passed", sa.Boolean(), nullable=True),
        sa.Column("evaluator_output_json", sa.Text(), nullable=True),
        sa.Column("evaluator_model_id", sa.String(length=100), nullable=True),
        sa.Column("display_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_recipe_verification_runs_user_id",
        "recipe_verification_runs",
        ["user_id"],
    )
    op.create_index(
        "ix_recipe_verification_runs_user_created",
        "recipe_verification_runs",
        ["user_id", "created_at"],
    )

    for table in _RLS_TABLES:
        op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))


def downgrade() -> None:
    for table in _RLS_TABLES:
        op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))
    op.drop_index(
        "ix_recipe_verification_runs_user_created",
        table_name="recipe_verification_runs",
    )
    op.drop_index(
        "ix_recipe_verification_runs_user_id",
        table_name="recipe_verification_runs",
    )
    op.drop_table("recipe_verification_runs")
