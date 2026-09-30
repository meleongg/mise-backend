"""Parse servings and compute safe scale factors (no unit conversion)."""

from __future__ import annotations

import re
from typing import Optional

_FRACTION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*$")
_LEADING_NUMBER_RE = re.compile(
    r"^\s*(\d+(?:\.\d+)?(?:\s*/\s*\d+(?:\.\d+)?)?)\s*(?:servings?)?\s*$",
    re.IGNORECASE,
)
_RANGE_RE = re.compile(r"\d+\s*-\s*\d+")


def parse_servings(value: Optional[str]) -> Optional[float]:
    """
    Parse a single serving count.

    Accepts integers, decimals, simple fractions, and bare "N servings".
    Rejects ranges (3-4), open-ended (6+), and labels like family — never invent
    a midpoint.
    """
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    lowered = raw.lower()
    if "+" in raw or _RANGE_RE.search(raw) or "family" in lowered:
        return None

    frac = _FRACTION_RE.match(raw)
    if frac:
        num = float(frac.group(1))
        den = float(frac.group(2))
        if den == 0:
            return None
        return num / den

    match = _LEADING_NUMBER_RE.match(raw)
    if not match:
        return None
    token = match.group(1).replace(" ", "")
    if "/" in token:
        parts = token.split("/", 1)
        try:
            num = float(parts[0])
            den = float(parts[1])
        except ValueError:
            return None
        if den == 0:
            return None
        return num / den
    try:
        return float(token)
    except ValueError:
        return None


def scale_factor(
    selected: Optional[str], baseline: Optional[str]
) -> tuple[float, bool, Optional[str]]:
    """
    Return (factor, needs_review, reason).

    Both parseable → selected/baseline. Otherwise factor 1.0 with review when
    either side was present but unparseable.
    """
    selected_n = parse_servings(selected)
    baseline_n = parse_servings(baseline)
    if selected_n is not None and baseline_n is not None and baseline_n != 0:
        return selected_n / baseline_n, False, None
    if (selected and selected_n is None) or (baseline and baseline_n is None):
        return (
            1.0,
            True,
            "servings not scaled; unparseable selected or baseline",
        )
    return 1.0, False, None
