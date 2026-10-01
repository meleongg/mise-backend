"""Parse servings and compute safe scale factors (no unit conversion)."""

from __future__ import annotations

import re
from typing import Optional, Union

ServingsInput = Union[str, int, float, None]

_FRACTION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*$")
_ABOUT_PREFIX_RE = re.compile(
    r"^\s*(?:about|approx\.?|approximately)\s+",
    re.IGNORECASE,
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


def parse_servings(value: ServingsInput) -> Optional[float]:
    """
    Coerce a serving count to float.

    Accepts numbers directly, plus legacy strings (N, N servings, Serves N).
    Rejects ranges / open-ended / family for live scale — never invent a midpoint.
    """
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
    if "+" in raw or _RANGE_RE.search(raw) or "family" in lowered:
        return None

    frac = _FRACTION_RE.match(raw)
    if frac:
        num = float(frac.group(1))
        den = float(frac.group(2))
        if den == 0:
            return None
        return num / den

    candidate = _strip_soft_noise(raw)
    match = _LEADING_NUMBER_RE.match(candidate)
    if not match:
        return None
    n = _parse_number_token(match.group(1))
    return n if n is not None and n > 0 else None


def coerce_servings_for_migration(value: ServingsInput) -> Optional[float]:
    """
    One-time migration helper: live parse, else low-end for ranges / open-ended.
    """
    parsed = parse_servings(value)
    if parsed is not None:
        return parsed
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    range_match = _RANGE_EXTRACT_RE.match(raw)
    if range_match:
        low = float(range_match.group(1))
        return low if low > 0 else None
    open_match = _OPEN_ENDED_RE.match(raw)
    if open_match:
        low = float(open_match.group(1))
        return low if low > 0 else None
    return None


def scale_factor(
    selected: ServingsInput, baseline: ServingsInput
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
    selected_present = selected is not None and str(selected).strip() != ""
    baseline_present = baseline is not None and str(baseline).strip() != ""
    if (selected_present and selected_n is None) or (
        baseline_present and baseline_n is None
    ):
        return (
            1.0,
            True,
            "servings not scaled; unparseable selected or baseline",
        )
    return 1.0, False, None
