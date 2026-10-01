"""Convert yield fields from varchar to nullable float (numeric servings).

Revision ID: j8e9f0a1
Revises: i7d8e9f0
Create Date: 2026-09-30 00:00:00.000000

Converts recipes.portion_size, personal_recipes.portion_size,
weekly_plan_entries.selected_servings, and users.preferred_portion_size
from free-text strings to nullable floats. Legacy strings are coerced with
live parse or low-end range policy; unparseable values become NULL.

Also rewrites recipe_snapshot JSON portion_size to a number when coercible.

Rollback risk: downgrade restores String(50) columns; numeric precision and
unparseable-null history are not perfectly reconstructed.
"""

from __future__ import annotations

import json
import re
from typing import Optional, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "j8e9f0a1"
down_revision: Union[str, Sequence[str], None] = "i7d8e9f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FRACTION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*$")
_ABOUT_PREFIX_RE = re.compile(
    r"^\s*(?:about|approx\.?|approximately)\s+", re.IGNORECASE
)
_SERVES_PREFIX_RE = re.compile(r"^\s*serves?\s+", re.IGNORECASE)
_LEADING_NUMBER_RE = re.compile(
    r"^\s*(\d+(?:\.\d+)?(?:\s*/\s*\d+(?:\.\d+)?)?)\s*"
    r"(?:servings?|people|persons?|ppl)?\s*$",
    re.IGNORECASE,
)
_RANGE_RE = re.compile(r"\d+\s*-\s*\d+")
_RANGE_EXTRACT_RE = re.compile(
    r"^\s*(?:(?:about|approx\.?|approximately)\s+)?(?:serves?\s+)?"
    r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)"
    r"\s*(?:servings?|people|persons?|ppl)?\s*$",
    re.IGNORECASE,
)
_OPEN_ENDED_RE = re.compile(
    r"^\s*(?:(?:about|approx\.?|approximately)\s+)?(?:serves?\s+)?"
    r"(\d+(?:\.\d+)?)\s*\+\s*"
    r"(?:servings?|people|persons?|ppl)?\s*$",
    re.IGNORECASE,
)


def _strip_soft_noise(raw: str) -> str:
    s = _ABOUT_PREFIX_RE.sub("", raw, count=1).strip()
    s = _SERVES_PREFIX_RE.sub("", s, count=1).strip()
    return s or raw


def _parse_number_token(token: str) -> Optional[float]:
    cleaned = token.replace(" ", "")
    if "/" in cleaned:
        parts = cleaned.split("/", 1)
        try:
            num = float(parts[0])
            den = float(parts[1])
        except ValueError:
            return None
        if den == 0:
            return None
        return num / den
    try:
        return float(cleaned)
    except ValueError:
        return None


def _coerce(value: object) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        n = float(value)
        return n if n > 0 else None
    raw = str(value).strip()
    if not raw:
        return None
    lowered = raw.lower()
    if "family" in lowered:
        return None

    frac = _FRACTION_RE.match(raw)
    if frac:
        den = float(frac.group(2))
        if den == 0:
            return None
        n = float(frac.group(1)) / den
        return n if n > 0 else None

    if _RANGE_RE.search(raw) or "+" in raw:
        range_match = _RANGE_EXTRACT_RE.match(raw)
        if range_match:
            low = float(range_match.group(1))
            return low if low > 0 else None
        open_match = _OPEN_ENDED_RE.match(raw)
        if open_match:
            low = float(open_match.group(1))
            return low if low > 0 else None
        return None

    candidate = _strip_soft_noise(raw)
    match = _LEADING_NUMBER_RE.match(candidate)
    if not match:
        return None
    n = _parse_number_token(match.group(1))
    return n if n is not None and n > 0 else None


def _convert_varchar_column(table: str, column: str) -> None:
    tmp = f"{column}_num"
    op.add_column(table, sa.Column(tmp, sa.Float(), nullable=True))
    conn = op.get_bind()
    rows = conn.execute(sa.text(f"SELECT id, {column} FROM {table}")).fetchall()
    for row_id, raw in rows:
        n = _coerce(raw)
        conn.execute(
            sa.text(f"UPDATE {table} SET {tmp} = :n WHERE id = :id"),
            {"n": n, "id": row_id},
        )
    op.drop_column(table, column)
    op.alter_column(table, tmp, new_column_name=column)


def _rewrite_snapshots() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, recipe_snapshot FROM weekly_plan_entries")
    ).fetchall()
    for row_id, snap_raw in rows:
        if not snap_raw:
            continue
        try:
            data = json.loads(snap_raw)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or "portion_size" not in data:
            continue
        coerced = _coerce(data.get("portion_size"))
        if coerced is None and data.get("portion_size") is None:
            continue
        data["portion_size"] = coerced
        conn.execute(
            sa.text(
                "UPDATE weekly_plan_entries SET recipe_snapshot = :snap WHERE id = :id"
            ),
            {"snap": json.dumps(data), "id": row_id},
        )


def upgrade() -> None:
    _convert_varchar_column("recipes", "portion_size")
    _convert_varchar_column("personal_recipes", "portion_size")
    _convert_varchar_column("weekly_plan_entries", "selected_servings")
    _convert_varchar_column("users", "preferred_portion_size")
    _rewrite_snapshots()


def downgrade() -> None:
    for table, column in (
        ("recipes", "portion_size"),
        ("personal_recipes", "portion_size"),
        ("weekly_plan_entries", "selected_servings"),
        ("users", "preferred_portion_size"),
    ):
        tmp = f"{column}_str"
        op.add_column(table, sa.Column(tmp, sa.String(length=50), nullable=True))
        conn = op.get_bind()
        rows = conn.execute(sa.text(f"SELECT id, {column} FROM {table}")).fetchall()
        for row_id, raw in rows:
            text = None
            if raw is not None:
                n = float(raw)
                text = str(int(n)) if n == int(n) else str(n)
            conn.execute(
                sa.text(f"UPDATE {table} SET {tmp} = :t WHERE id = :id"),
                {"t": text, "id": row_id},
            )
        op.drop_column(table, column)
        op.alter_column(table, tmp, new_column_name=column)
