"""Parsing and validation of `minutes_after_open` from market payloads."""

from __future__ import annotations

import math
from typing import Any


def parse_minutes_after_open(raw: Any) -> int | None:
    """
    Return whole minutes after the open, or None if missing / NaN / not parseable.
    """
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if math.isnan(value):
        return None
    return int(value)
