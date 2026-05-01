"""Higher-timeframe (intraday session) regime filter.

Computes an EMA on the recent close series to gate entries against the broader
session trend. The 1-min `trend_score` from the bridge captures very short-term
direction; this layer captures direction over the last ~hour.

Adaptive behavior
-----------------
Early in the session (< 60 bars / ~1 hour of 1-min data) there is not enough
history to compute the primary EMA50 reliably. Rather than blocking all entries
during the ORB breakout window (8:45–9:00 CT), `htf_regime_adaptive` falls back
to EMA20, which only needs ~30 bars. Once 60+ bars are available, EMA50 takes
over for the rest of the session.

Call `htf_regime_adaptive` from application code.
Use `htf_regime` directly only when you need a fixed-period result (e.g. tests).
"""

from __future__ import annotations

from typing import Any

from app.constants import HTF_EMA_PERIOD, HTF_EMA_PERIOD_EARLY, HTF_SLOPE_LOOKBACK


def _ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(alpha * v + (1 - alpha) * out[-1])
    return out


def htf_regime(
    recent_bars: list[dict[str, Any]],
    period: int = HTF_EMA_PERIOD,
    slope_lookback: int = HTF_SLOPE_LOOKBACK,
) -> tuple[bool, bool]:
    """Return (uptrend, downtrend) for a fixed EMA period.

    Both can be False (chop / not enough data). Both should never be True.
    Returns (False, False) when there are fewer than `period + slope_lookback` bars.
    """
    if len(recent_bars) < period + slope_lookback:
        return (False, False)

    closes = [float(b["close"]) for b in recent_bars]
    ema = _ema(closes, period)
    if len(ema) < slope_lookback + 1:
        return (False, False)

    current_close = closes[-1]
    current_ema = ema[-1]
    past_ema = ema[-(slope_lookback + 1)]

    is_up = current_close > current_ema and current_ema > past_ema
    is_down = current_close < current_ema and current_ema < past_ema
    return (is_up, is_down)


def htf_regime_adaptive(
    recent_bars: list[dict[str, Any]],
    slope_lookback: int = HTF_SLOPE_LOOKBACK,
) -> tuple[bool, bool, int]:
    """Adaptive HTF regime check that graduates from EMA20 → EMA50 as bars accumulate.

    Returns (uptrend, downtrend, ema_period_used).

    Selection logic:
      - >= 60 bars (HTF_EMA_PERIOD + slope_lookback): use EMA50 — full, stable reading.
      - >= 30 bars (HTF_EMA_PERIOD_EARLY + slope_lookback): use EMA20 — early-session
        proxy. Noisier but better than blocking the 8:45–9:00 CT ORB window entirely.
      - < 30 bars: return (False, False, 0) — truly insufficient data; both False means
        the caller should block entries, not allow them freely.

    The graduation threshold is deterministic (bar count), so there is no jump at the
    boundary — EMA20 and EMA50 will generally agree on trend direction once the EMA50
    has enough warmup data, since longer-period EMAs lag and confirm rather than contradict.
    """
    full_warmup = HTF_EMA_PERIOD + slope_lookback      # 60 bars / ~1 hr
    early_warmup = HTF_EMA_PERIOD_EARLY + slope_lookback  # 30 bars / ~30 min

    if len(recent_bars) >= full_warmup:
        # Enough history for the primary EMA50 — use it for the rest of the session.
        up, down = htf_regime(recent_bars, HTF_EMA_PERIOD, slope_lookback)
        return (up, down, HTF_EMA_PERIOD)

    if len(recent_bars) >= early_warmup:
        # Early session: EMA50 is still cold; use EMA20 as a faster proxy.
        # This covers the ORB breakout window (8:45–9:15 CT) where missing
        # a valid setup is more costly than the slightly noisier trend read.
        up, down = htf_regime(recent_bars, HTF_EMA_PERIOD_EARLY, slope_lookback)
        return (up, down, HTF_EMA_PERIOD_EARLY)

    # Truly not enough data (first ~30 min of the session).
    # (False, False) signals callers to block entries — do not treat as "neutral/allow".
    return (False, False, 0)
