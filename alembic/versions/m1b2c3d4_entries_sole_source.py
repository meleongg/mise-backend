"""Make weekly_plan_entries the sole plan membership source of truth

Revision ID: m1b2c3d4
Revises: l0a1b2c3
Create Date: 2026-10-01 12:00:00.000000

Backfills missing weekly_plan_entries from legacy recipe_schedule JSON, then
drops weekly_plans.recipe_schedule.

Rollback risk: re-adds empty recipe_schedule column; entry data is kept.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import text

revision: str = "m1b2c3d4"
down_revision: Union[str, Sequence[str], None] = "l0a1b2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Backfill entries for plans that have schedule JSON but zero entry rows.
    plans = conn.execute(
        text(
            """
            SELECT wp.id, wp.recipe_schedule
            FROM weekly_plans wp
            WHERE NOT EXISTS (
                SELECT 1 FROM weekly_plan_entries e
                WHERE e.weekly_plan_id = wp.id
            )
            AND wp.recipe_schedule IS NOT NULL
            AND TRIM(wp.recipe_schedule) <> ''
            AND TRIM(wp.recipe_schedule) <> '[]'
            """
        )
    ).fetchall()

    for plan_id, schedule_raw in plans:
        try:
            import json

            schedule = json.loads(schedule_raw)
        except Exception:
            continue
        if not isinstance(schedule, list):
            continue
        ordered = sorted(
            [item for item in schedule if isinstance(item, dict)],
            key=lambda x: x.get("order", 0),
        )
        for idx, item in enumerate(ordered):
            recipe_id = item.get("recipe_id")
            if not recipe_id:
                continue
            exists = conn.execute(
                text("SELECT 1 FROM recipes WHERE id = :rid"),
                {"rid": recipe_id},
            ).fetchone()
            if not exists:
                continue
            recipe_row = conn.execute(
                text(
                    """
                    SELECT id, name, cuisine, difficulty, ingredients, instructions,
                           prep_time_minutes, cook_time_minutes, portion_size, image_url
                    FROM recipes WHERE id = :rid
                    """
                ),
                {"rid": recipe_id},
            ).mappings().first()
            if not recipe_row:
                continue
            snapshot = {
                "id": str(recipe_row["id"]),
                "name": recipe_row["name"],
                "cuisine": recipe_row["cuisine"],
                "difficulty": recipe_row["difficulty"],
                "ingredients": recipe_row["ingredients"],
                "instructions": recipe_row["instructions"],
                "prep_time_minutes": recipe_row["prep_time_minutes"],
                "cook_time_minutes": recipe_row["cook_time_minutes"],
                "portion_size": recipe_row["portion_size"],
                "image_url": recipe_row["image_url"],
            }
            import json
            import uuid as uuid_mod

            conn.execute(
                text(
                    """
                    INSERT INTO weekly_plan_entries (
                        id, weekly_plan_id, position, catalog_recipe_id,
                        personal_recipe_id, recipe_snapshot, selected_servings,
                        lifecycle_state, created_at, updated_at
                    ) VALUES (
                        :id, :plan_id, :position, :catalog_id,
                        NULL, :snapshot, :servings,
                        'planned', NOW(), NOW()
                    )
                    """
                ),
                {
                    "id": str(uuid_mod.uuid4()),
                    "plan_id": str(plan_id),
                    "position": idx,
                    "catalog_id": str(recipe_row["id"]),
                    "snapshot": json.dumps(snapshot, default=str),
                    "servings": recipe_row["portion_size"],
                },
            )

    op.drop_column("weekly_plans", "recipe_schedule")


def downgrade() -> None:
    op.add_column(
        "weekly_plans",
        sa.Column(
            "recipe_schedule",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
    )
    # Best-effort rebuild schedule JSON from entries
    conn = op.get_bind()
    conn.execute(
        text(
            """
            UPDATE weekly_plans wp
            SET recipe_schedule = COALESCE((
                SELECT json_agg(
                    json_build_object(
                        'recipe_id', e.catalog_recipe_id::text,
                        'order', e.position
                    )
                    ORDER BY e.position
                )::text
                FROM weekly_plan_entries e
                WHERE e.weekly_plan_id = wp.id
                  AND e.catalog_recipe_id IS NOT NULL
            ), '[]')
            """
        )
    )
    op.alter_column("weekly_plans", "recipe_schedule", server_default=None)
