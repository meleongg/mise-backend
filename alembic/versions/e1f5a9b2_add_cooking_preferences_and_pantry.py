"""add cooking preferences and pantry

Revision ID: e1f5a9b2
Revises: d4e5f6a7
"""

from alembic import op
import sqlalchemy as sa

revision = "e1f5a9b2"
down_revision = "d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("city", sa.String(length=100), nullable=True))
    op.add_column("users", sa.Column("preferred_retailer", sa.String(length=100), nullable=True))
    op.add_column("users", sa.Column("sodie_memory_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("chat_retention_policy", sa.String(length=20), nullable=False, server_default="18_months"))
    inspector = sa.inspect(op.get_bind())
    if "user_pantry_items" not in inspector.get_table_names():
        op.create_table("user_pantry_items", sa.Column("id", sa.String(length=36), primary_key=True), sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False), sa.Column("name", sa.String(length=200), nullable=False), sa.Column("is_baseline", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("created_at", sa.DateTime(), nullable=True))
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("user_pantry_items")}
    if "ix_user_pantry_items_user_id" not in indexes:
        op.create_index("ix_user_pantry_items_user_id", "user_pantry_items", ["user_id"])


def downgrade():
    op.drop_index("ix_user_pantry_items_user_id", table_name="user_pantry_items")
    op.drop_table("user_pantry_items")
    op.drop_column("users", "chat_retention_policy")
    op.drop_column("users", "sodie_memory_enabled")
    op.drop_column("users", "preferred_retailer")
    op.drop_column("users", "city")
