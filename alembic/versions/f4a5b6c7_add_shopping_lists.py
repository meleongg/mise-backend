"""Add shopping_lists / items / sources for weekly shopping mode

Revision ID: f4a5b6c7
Revises: e3f4a5b6
Create Date: 2026-09-27 00:00:00.000000

Creates shopping list tables sourced from weekly_plan_entries. Serving
scaler and pantry subtraction stay out of this revision.

RLS posture matches prior tables: ENABLE without permissive policies and
without FORCE.

Rollback risk: downgrade drops shopping tables (and RLS). Re-apply only
after confirming no active shopping clients depend on the schema.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "f4a5b6c7"
down_revision: Union[str, Sequence[str], None] = "e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "shopping_lists",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "weekly_plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("weekly_plans.id"),
            nullable=True,
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="active"
        ),
        sa.Column("retailer_snapshot", sa.String(length=100), nullable=True),
        sa.Column("location_snapshot", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_shopping_lists_user_id", "shopping_lists", ["user_id"])
    op.create_index(
        "ix_shopping_lists_weekly_plan_id", "shopping_lists", ["weekly_plan_id"]
    )

    op.create_table(
        "shopping_list_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "shopping_list_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("shopping_lists.id"),
            nullable=False,
        ),
        sa.Column("normalized_name", sa.String(length=200), nullable=False),
        sa.Column("display_text", sa.String(length=300), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=True),
        sa.Column("unit", sa.String(length=50), nullable=True),
        sa.Column("aisle", sa.String(length=80), nullable=True),
        sa.Column(
            "is_checked", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "is_user_edit",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "needs_review",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("confidence", sa.String(length=20), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_shopping_list_items_shopping_list_id",
        "shopping_list_items",
        ["shopping_list_id"],
    )

    op.create_table(
        "shopping_list_item_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "shopping_list_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("shopping_list_items.id"),
            nullable=False,
        ),
        sa.Column(
            "weekly_plan_entry_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("weekly_plan_entries.id"),
            nullable=False,
        ),
        sa.Column("source_amount", sa.String(length=100), nullable=True),
        sa.Column(
            "inclusion_state",
            sa.String(length=20),
            nullable=False,
            server_default="included",
        ),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_shopping_list_item_sources_item_id",
        "shopping_list_item_sources",
        ["shopping_list_item_id"],
    )
    op.create_index(
        "ix_shopping_list_item_sources_entry_id",
        "shopping_list_item_sources",
        ["weekly_plan_entry_id"],
    )

    op.execute("ALTER TABLE shopping_lists ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE shopping_list_items ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE shopping_list_item_sources ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("ALTER TABLE shopping_list_item_sources DISABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE shopping_list_items DISABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE shopping_lists DISABLE ROW LEVEL SECURITY")
    op.drop_index(
        "ix_shopping_list_item_sources_entry_id",
        table_name="shopping_list_item_sources",
    )
    op.drop_index(
        "ix_shopping_list_item_sources_item_id",
        table_name="shopping_list_item_sources",
    )
    op.drop_table("shopping_list_item_sources")
    op.drop_index(
        "ix_shopping_list_items_shopping_list_id", table_name="shopping_list_items"
    )
    op.drop_table("shopping_list_items")
    op.drop_index("ix_shopping_lists_weekly_plan_id", table_name="shopping_lists")
    op.drop_index("ix_shopping_lists_user_id", table_name="shopping_lists")
    op.drop_table("shopping_lists")
