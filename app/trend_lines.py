"""Trend-line tracking: rolling linear-regression fit on bar closes.

The fitted value at each bar is the y-value the regression line takes at that
bar's position in its own trailing window. Plotted as a connected line, the
result is a smoothed price curve whose slope visualizes the prevailing short-
term trend (rising = uptrend, falling = downtrend, flat = chop).

Used for the dashboard visualization only. Slope and extension filters were
tested as strategy gates and rejected on the current 15-day uptrending dataset
— see CLAUDE.md "What we tried that doesn't work". Revisit when the dataset
includes downtrend / chop sessions; X=1.0 extension cap on W=30 is the most
defensible candidate to retry.
"""

from __future__ import annotations

from typing import Any

TREND_LINE_WINDOW = 30  # bars


def compute_trend_line(
    bars: list[dict[str, Any]],
    window: int = TREND_LINE_WINDOW,
) -> list[dict[str, Any]]:
    """Return [{"timestamp": ts, "value": fitted_close}, ...] for each bar that
    has a full trailing window of `window` closes available. Bars near the
    start of the input that lack history are omitted.
    """
    out: list[dict[str, Any]] = []
    n = len(bars)
    if window < 2 or n < window:
        return out

    xm = (window - 1) / 2.0
    den = sum((j - xm) ** 2 for j in range(window))
    if den == 0:
        return out

    for i in range(window - 1, n):
        win = bars[i - window + 1 : i + 1]
        closes: list[float] = []
        for b in win:
            c = b.get("close")
            if c is None:
                break
            closes.append(float(c))
        if len(closes) < window:
            continue

        ym = sum(closes) / window
        num = sum((j - xm) * (c - ym) for j, c in enumerate(closes))
        slope = num / den
        intercept = ym - slope * xm
        fitted = intercept + slope * (window - 1)

        out.append({"timestamp": bars[i]["timestamp"], "value": float(fitted)})

    return out
