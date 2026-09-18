"""add persistent sodie threads

Revision ID: f2b8c1d4
Revises: e1f5a9b2
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "f2b8c1d4"
down_revision = "e1f5a9b2"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("sodie_threads", sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False), sa.Column("scope", sa.String(20), nullable=False), sa.Column("context_id", sa.String(36)), sa.Column("is_temporary", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("created_at", sa.DateTime(), nullable=True), sa.Column("updated_at", sa.DateTime(), nullable=True))
    op.create_index("ix_sodie_threads_user_id", "sodie_threads", ["user_id"])
    op.create_table("sodie_messages", sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("thread_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sodie_threads.id"), nullable=False), sa.Column("sender", sa.String(10), nullable=False), sa.Column("content", sa.Text(), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=True))
    op.create_index("ix_sodie_messages_thread_id", "sodie_messages", ["thread_id"])

def downgrade():
    op.drop_index("ix_sodie_messages_thread_id", table_name="sodie_messages"); op.drop_table("sodie_messages")
    op.drop_index("ix_sodie_threads_user_id", table_name="sodie_threads"); op.drop_table("sodie_threads")
