"""Intraday time-bucket labels for analytics (minutes since RTH open)."""

from __future__ import annotations

import math
from typing import Any


def time_bucket_label(minutes: Any) -> str:
    try:
        m = float(minutes)
    except (TypeError, ValueError):
        return "INVALID"

    if math.isnan(m):
        return "INVALID"

    im = int(m)

    if im < 30:
        return "0-30"
    if im < 60:
        return "30-60"
    if im < 90:
        return "60-90"
    if im < 120:
        return "90-120"
    if im < 150:
        return "120-150"
    if im < 180:
        return "150-180"
    if im < 240:
        return "180-240"
    if im < 300:
        return "240-300"
    if im < 390:
        return "300-390"
    return "390+"
