"""Application-wide constants"""

from __future__ import annotations

# --- PnL / instrument (ES-style index) ---
POINT_VALUE = 50.0

# --- Execution simulation (backtests) ---
ES_TICK_SIZE = 0.25
ES_SLIPPAGE_TICKS_PER_SIDE = 1
SLIPPAGE_POINTS_PER_SIDE = ES_SLIPPAGE_TICKS_PER_SIDE * ES_TICK_SIZE

# --- API / HTTP ---
# Local dashboard only — no auth cookies are used, so wildcard is safe here.
CORS_ALLOW_ORIGINS: tuple[str, ...] = ("*",)

HEALTH_STATUS_OK = "ok"

# --- Live bridge / ES: block orders if payload fields disagree beyond this (index points) ---
DESYNC_PRICE_GUARD_MAX_POINTS = 3.0

# --- Position / bridge payloads ---
POSITION_ACTION_UPDATE_STOP = "UPDATE_STOP"
MANAGE_POSITION_RESPONSE_NEW_STOP = "new_stop_loss"
POSITION_KEY_ENTRY_PRICE = "entry_price"
POSITION_KEY_CURRENT_PRICE = "current_price"
POSITION_KEY_STOP_LOSS = "stop_loss"
POSITION_KEY_INITIAL_STOP = "initial_stop"
POSITION_KEY_ACTION = "action"
POSITION_KEY_ATR = "atr"

# --- Market session ---
MINUTES_AFTER_OPEN_INVALID_SENTINEL = 999
MINUTES_AFTER_OPEN_ORB_CONTEXT = 15

# Session windows blocked in `decide()` — [start, end) minutes after open
SESSION_WEAK_OPENING_START = 30
SESSION_WEAK_OPENING_END = 90  # extended: 60-89 min has ~10% win rate in backtest

SESSION_WEAK_MID_A_START = 90
SESSION_WEAK_MID_A_END = 180  # extended: 150-179 min has 0% win rate in backtest

# 180-240 min (former MID_B) is intentionally unblocked: backtest shows 52% win rate
# 240-300 min has 63% win rate — best window of the session

# ORB: no new entries at or after this many minutes after the open (e.g. late session).
SESSION_ORB_NO_ENTRY_MINUTES_AFTER_OPEN = 360

# --- Decision filters ---
CHOP_SCORE_MAX = 0.55
TREND_SCORE_MIN = 0.90  # raised from 0.65: strong trend gives 47% vs 37% at 0.7

# --- ORB strategy ---
STRATEGY_ORB_ATR_MULTIPLIER = 1.25        # stop width for mid/late session
STRATEGY_ORB_EARLY_ATR_MULTIPLIER = 1.75  # wider stop for first 30 min; opening range has
                                           # elevated volatility that 1.25x ATR doesn't cover
STRATEGY_ORB_EARLY_SESSION_MINUTES = 30   # bars below this threshold use the wider multiplier
STRATEGY_ORB_REWARD_RISK_RATIO = 2.0

# Maximum downward wick (for longs) or upward wick (for shorts) on the entry bar,
# expressed as a multiple of ATR. If the bar already spiked this far through the
# stop zone during the entry candle, the breakout is considered unstable and the
# signal is skipped. Protects against "stop-hunt then breakout" entries where the
# stop is hit before the move takes off.
ORB_ENTRY_MAX_WICK_ATR = 2.0

ORB_LONG_SCORE_THRESHOLD = 0.70
ORB_SHORT_SCORE_THRESHOLD = 0.70
ORB_LONG_ML_THRESHOLD = 0.55
ORB_SHORT_ML_THRESHOLD = 0.45

ORB_TREND_SCORE_FOR_WEIGHT = 0.7
ORB_VOLUME_SURGE_MULTIPLIER = 1.25

# Long score weights
ORB_LONG_WEIGHT_BREAK_ORB = 0.30
ORB_LONG_WEIGHT_ABOVE_VWAP = 0.20
ORB_LONG_WEIGHT_TREND = 0.20
ORB_LONG_WEIGHT_VOLUME = 0.15
ORB_LONG_WEIGHT_RSI = 0.10
ORB_LONG_WEIGHT_TIME = 0.05
ORB_LONG_RSI_LOW = 55
ORB_LONG_RSI_HIGH = 72

# Short score weights (same structure as long)
ORB_SHORT_WEIGHT_BREAK_ORB = 0.30
ORB_SHORT_WEIGHT_BELOW_VWAP = 0.20
ORB_SHORT_WEIGHT_TREND = 0.20
ORB_SHORT_WEIGHT_VOLUME = 0.15
ORB_SHORT_WEIGHT_RSI = 0.10
ORB_SHORT_WEIGHT_TIME = 0.05
ORB_SHORT_RSI_LOW = 28
ORB_SHORT_RSI_HIGH = 45

ORB_RISK_BASE_MAX_POINTS = 7.5

# --- Position sizing (ORB) ---
QUANTITY_MAX_RISK_DOLLARS_DEFAULT = 100.0
QUANTITY_MAX_CONTRACTS_DEFAULT = 3

# --- Anti-martingale: scale down or halt after consecutive losses ---
LOSS_STREAK_REDUCE_QTY_THRESHOLD = 2  # cap quantity at 1 contract after this many losses
LOSS_STREAK_HALT_THRESHOLD = 3  # block all entries for the rest of the day after this many losses

# --- Higher-timeframe regime filter (EMA on 1-min closes from recent_bars) ---
# The filter uses two EMA periods to handle the early-session warmup gap:
#
#   EMA20 (early window): kicks in after ~30 bars (~30 min). Used to detect trend
#   direction as soon as possible after the ORB window closes at 8:45 CT.
#   Shorter period means noisier readings but better than blocking all early entries.
#
#   EMA50 (full window): kicks in after ~60 bars (~1 hour). More stable, less
#   susceptible to false signals mid-session. This is the primary implementation.
#
# The adaptive function switches from EMA20 → EMA50 automatically once enough
# bars accumulate, so there is no abrupt hard cutoff in filter behavior.
HTF_EMA_PERIOD_EARLY = 20   # warmup: 20 + 10 = 30 bars (~30 min after open)
HTF_EMA_PERIOD = 50         # full: 50 + 10 = 60 bars (~1 hour after open)
HTF_SLOPE_LOOKBACK = 10     # bars; EMA must be trending this many bars ago

# --- VWAP reversion strategy ---
VWAP_REVERSION_PULLBACK_LOOKBACK = 5  # bars to scan for the pullback close below VWAP
VWAP_REVERSION_MAX_DISTANCE_ATR = 0.75  # current price must be within this many ATRs of VWAP
VWAP_REVERSION_MIN_BODY_RATIO = 0.55   # bounce bar body must be >=55% of its range
# Upper wick must be <= this fraction of bar range; a large upper wick means price gave back
# the bounce, signaling weak buyers.
VWAP_REVERSION_MAX_UPPER_WICK_RATIO = 0.25

# --- Trailing stop (adjust_trailing_stop) ---
TRAIL_PROFIT_MULTIPLIER_TIER_1 = 1.0
TRAIL_PROFIT_MULTIPLIER_TIER_2 = 1.5
TRAIL_PROFIT_MULTIPLIER_TIER_3 = 2.0
TRAIL_ATR_TIGHTEN_MULTIPLIER = 0.75
TRAIL_LOCK_FRACTION_OF_INITIAL = 0.5

# --- Backtest ---
BACKTEST_MAX_LOOKAHEAD_BARS = 20
# Must be >= HTF_EMA_PERIOD + HTF_SLOPE_LOOKBACK (60) for the full EMA50 to engage.
# Keeping at 120 gives the early-session EMA20 (30 bars) room to warm up well before
# the EMA50 takes over, matching the buffer the live feed builds up during pre-market.
BACKTEST_RECENT_BARS_WINDOW = 120
BACKTEST_SKIP_BARS_AFTER_TRADE = 5

# --- API data access ---
# Bumped from 50 → 120 so HTF EMA50 + slope lookback have enough history to be stable.
RECENT_BARS_QUERY_LIMIT_DEFAULT = 120


def orb_max_risk_points(minutes_after_open: int) -> float:
    """Dynamic stop-width cap (points) from time-of-day."""
    m = minutes_after_open
    if 150 <= m < 180:
        mult = 1.3
    elif 240 <= m < 300:
        mult = 1.0
    elif 300 <= m <= 390:
        mult = 1.1
    else:
        mult = 0.8
    return ORB_RISK_BASE_MAX_POINTS * mult
