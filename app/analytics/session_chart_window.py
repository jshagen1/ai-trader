"""RTH window for the session chart: 08:30–16:30 America/Chicago (CST/CDT)."""

from __future__ import annotations

from datetime import time
from typing import Any

import pandas as pd
from zoneinfo import ZoneInfo

SESSION_CHART_TZ = ZoneInfo("America/Chicago")
SESSION_CHART_OPEN_CT = time(8, 30)
SESSION_CHART_CLOSE_CT = time(16, 30)


def timestamp_in_session_chart_chicago(ts: Any) -> bool:
    """True when the bar's local time in Chicago is within 08:30–16:30 inclusive.

    Naive datetimes are interpreted as America/Chicago wall time (matches typical
    NinjaTrader / CSV bar stamps for ES). Aware values are converted to Chicago.
    """
    dt = pd.to_datetime(ts, errors="coerce")
    if pd.isna(dt):
        return False
    ts_p = pd.Timestamp(dt)
    if ts_p.tzinfo is None:
        ts_p = ts_p.tz_localize(SESSION_CHART_TZ)
    else:
        ts_p = ts_p.tz_convert(SESSION_CHART_TZ)
    clock = ts_p.time()
    return SESSION_CHART_OPEN_CT <= clock <= SESSION_CHART_CLOSE_CT
