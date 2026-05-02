"""Re-run the decision engine over one trading date and return candle+decision data."""

from __future__ import annotations

from collections import OrderedDict
from threading import Lock
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.analytics.session_chart_window import timestamp_in_session_chart_chicago
from app.constants import (
    BACKTEST_MAX_LOOKAHEAD_BARS,
    BACKTEST_RECENT_BARS_WINDOW,
    BACKTEST_SKIP_BARS_AFTER_TRADE,
    SESSION_CHART_SESSION_MIN_BARS_THRESHOLD,
)
from app.decision_engine import DecisionEngine
from app.enums import Action, ExitReason
from app.pnl import calculate_pnl
from app.scripts.backtest_from_bars import simulate_exit, to_market_state
from app.slippage import apply_entry_slippage, apply_exit_slippage
from app.trend_lines import compute_trend_line


def list_available_dates(db: Session) -> list[str]:
    """Distinct YYYY-MM-DD dates with a *complete, usable* session, newest first.

    A session is considered usable when:
      - At least 14 of 15 ORB-window bars (08:30-08:44 Chicago) are present
      - At least SESSION_CHART_SESSION_MIN_BARS_THRESHOLD bars in 08:30-16:30 Chicago
      - At least 95% of those in-window bars have non-null indicators
        (vwap, atr, rsi, orb_high, orb_low, trend_score, chop_score)

    Timestamp strings are assumed Chicago wall clock (naive ``YYYY-MM-DD HH:MM:SS``),
    matching NinjaTrader exports and ``substr(..., 12, 5)`` time extraction.

    Filters out empty placeholder dates, holidays, and partial sessions that
    would render a broken/empty chart in the dashboard.
    """
    thresh = int(SESSION_CHART_SESSION_MIN_BARS_THRESHOLD)
    rows = db.execute(
        text(
            f"""
            WITH session_stats AS (
              SELECT
                substr(timestamp, 1, 10) AS d,
                SUM(CASE
                      WHEN substr(timestamp, 12, 5) >= '08:30'
                       AND substr(timestamp, 12, 5) <= '08:44' THEN 1 ELSE 0
                    END) AS orb_count,
                SUM(CASE
                      WHEN substr(timestamp, 12, 5) >= '08:30'
                       AND substr(timestamp, 12, 5) <= '16:30' THEN 1 ELSE 0
                    END) AS sess_count,
                SUM(CASE
                      WHEN substr(timestamp, 12, 5) >= '08:30'
                       AND substr(timestamp, 12, 5) <= '16:30'
                       AND vwap IS NOT NULL AND atr IS NOT NULL
                       AND rsi IS NOT NULL AND orb_high IS NOT NULL
                       AND orb_low IS NOT NULL AND trend_score IS NOT NULL
                       AND chop_score IS NOT NULL
                      THEN 1 ELSE 0
                    END) AS clean_count
              FROM market_bars
              WHERE timestamp IS NOT NULL
              GROUP BY d
            )
            SELECT d FROM session_stats
            WHERE orb_count >= 14
              AND sess_count >= {thresh}
              AND clean_count * 100 >= sess_count * 95
            ORDER BY d DESC
            """
        )
    ).fetchall()
    return [r[0] for r in rows if r[0]]


def _bar_to_payload(row: pd.Series) -> dict[str, Any]:
    return {
        "timestamp": row["timestamp"],
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
        "volume": float(row["volume"] or 0),
    }


# --- Cache ---------------------------------------------------------------
# Keyed by (date_str, schema_version). Value: (latest_bar_id, payload).
# - Past dates: latest_bar_id never changes → entry stays valid forever.
# - Today: new bars push latest_bar_id forward → next call recomputes naturally.
# - Schema changes: bump _PAYLOAD_SCHEMA_VERSION → all old entries auto-invalidate.
# - Strategy code changes: cache is in-memory, gone after `uvicorn --reload` / restart.
_SESSION_CACHE_MAX = 64  # ~6 MB worst case at ~90 KB per entry
_PAYLOAD_SCHEMA_VERSION = 5  # bumped: session chart bars clipped to 08:30–16:30 Chicago
_session_cache: OrderedDict[tuple[str, int], tuple[int, dict[str, Any]]] = OrderedDict()
_session_cache_lock = Lock()
_session_cache_hits = 0
_session_cache_misses = 0


def _cache_get(date_str: str, latest_id: int) -> dict[str, Any] | None:
    global _session_cache_hits
    key = (date_str, _PAYLOAD_SCHEMA_VERSION)
    with _session_cache_lock:
        entry = _session_cache.get(key)
        if entry is not None and entry[0] == latest_id:
            _session_cache.move_to_end(key)
            _session_cache_hits += 1
            return entry[1]
        return None


def _cache_set(date_str: str, latest_id: int, payload: dict[str, Any]) -> None:
    global _session_cache_misses
    key = (date_str, _PAYLOAD_SCHEMA_VERSION)
    with _session_cache_lock:
        _session_cache[key] = (latest_id, payload)
        _session_cache.move_to_end(key)
        while len(_session_cache) > _SESSION_CACHE_MAX:
            _session_cache.popitem(last=False)
        _session_cache_misses += 1


def cache_stats() -> dict[str, int]:
    with _session_cache_lock:
        return {
            "size": len(_session_cache),
            "hits": _session_cache_hits,
            "misses": _session_cache_misses,
        }


def clear_cache() -> None:
    with _session_cache_lock:
        _session_cache.clear()


def _latest_bar_id_for_date(db: Session, date_str: str) -> int | None:
    """Cheap O(log n) probe — uses the timestamp index via range comparison.

    Range works for all three timestamp formats present in the DB
    ("YYYY-MM-DD ...", "YYYY-MM-DDTHH:..." with or without offset) because they
    all sort within `[date_str, next_date_str)`.
    """
    next_day_prefix = chr(ord(date_str[-1]) + 1) if date_str[-1] != "9" else None
    # Easier and just as correct: lexicographic next_day = date_str + "z" sentinel
    # ('z' is greater than any digit/space/T/-/+ that can legally follow a date prefix).
    return db.execute(
        text(
            "SELECT MAX(id) FROM market_bars "
            "WHERE timestamp >= :start AND timestamp < :stop"
        ),
        {"start": date_str, "stop": date_str + "z"},
    ).scalar()


def session_view(db: Session, date_str: str) -> dict[str, Any]:
    """Bars + decisions for a single calendar date, plus session ORB levels.

    The engine is rerun on the fly so this always reflects the current strategy
    logic — no dependency on whether the user has run a backtest recently.

    Results are memoized per (date, latest_bar_id) so revisiting a date is free.
    """
    latest_id = _latest_bar_id_for_date(db, date_str)
    if latest_id is None:
        return {"date": date_str, "bars": [], "decisions": [], "orb": None, "trend_line": []}

    cached = _cache_get(date_str, latest_id)
    if cached is not None:
        return cached

    payload = _compute_session_view(db, date_str)
    _cache_set(date_str, latest_id, payload)
    return payload


def _compute_session_view(db: Session, date_str: str) -> dict[str, Any]:
    # Order by timestamp, not id. Backfilled bars are inserted after live-feed bars
    # for the same session, so their ids are higher even though they're chronologically
    # earlier. Sorting by id would put 8:30-8:34 backfill bars after 10:49 live bars,
    # breaking the chart and the decisions loop's exit-simulation lookahead.
    bars_all = pd.read_sql(
        "SELECT * FROM market_bars ORDER BY timestamp ASC",
        db.bind,
    )
    if bars_all.empty:
        return {"date": date_str, "bars": [], "decisions": [], "orb": None, "trend_line": []}

    bars_all = bars_all.dropna(
        subset=[
            "open", "high", "low", "close", "volume",
            "vwap", "atr", "rsi", "orb_high", "orb_low",
            "trend_score", "chop_score",
        ]
    ).reset_index(drop=True)

    in_date = bars_all["timestamp"].astype(str).str.startswith(date_str)
    if not in_date.any():
        return {"date": date_str, "bars": [], "decisions": [], "orb": None, "trend_line": []}

    date_indices = bars_all.index[in_date].tolist()
    first_idx = date_indices[0]
    last_idx = date_indices[-1]

    engine = DecisionEngine()

    decisions: list[dict[str, Any]] = []
    skip_until = -1

    for i in range(first_idx, last_idx + 1):
        if i + BACKTEST_MAX_LOOKAHEAD_BARS >= len(bars_all):
            break  # no future bars to simulate exits against
        if i <= skip_until:
            continue
        if not timestamp_in_session_chart_chicago(bars_all.at[i, "timestamp"]):
            continue

        recent_start = max(0, i - BACKTEST_RECENT_BARS_WINDOW)
        recent_bars = bars_all.iloc[recent_start:i].to_dict("records")
        market = to_market_state(bars_all.iloc[i])

        signal = engine.decide(market, recent_bars)

        if signal.action not in (Action.BUY.value, Action.SELL.value):
            continue

        exit_price, exit_reason, exit_time = simulate_exit(bars_all, i, signal)

        # Match the backtest's PnL accounting: apply per-side slippage, then
        # calculate dollar PnL for the actual quantity the engine sized.
        quantity = getattr(signal, "quantity", 1) or 1
        slipped_entry = apply_entry_slippage(signal.action, signal.entry)
        slipped_exit = apply_exit_slippage(signal.action, exit_price)
        pnl = calculate_pnl(signal.action, slipped_entry, slipped_exit, quantity)

        decisions.append({
            "entry_time": market.timestamp,
            "exit_time": exit_time,
            "action": signal.action,
            "strategy": signal.strategy,
            "confidence": signal.confidence,
            "entry": signal.entry,
            "stop_loss": signal.stop_loss,
            "take_profit": signal.take_profit,
            "exit_price": exit_price,
            "exit_reason": exit_reason,
            "reason": signal.reason,
            "minutes_after_open": market.minutes_after_open,
            "quantity": quantity,
            "pnl": pnl,
        })

        skip_until = i + BACKTEST_SKIP_BARS_AFTER_TRADE

    date_bars = bars_all.iloc[first_idx : last_idx + 1]
    chart_mask = date_bars["timestamp"].map(timestamp_in_session_chart_chicago)
    session_chart_bars = date_bars[chart_mask]
    bars_payload = [_bar_to_payload(row) for _, row in session_chart_bars.iterrows()]

    # ORB is only valid after the 08:30-08:45 window closes (minutes_after_open >= 15).
    # Pre-open / overnight bars have orb_high == orb_low == close as a fallback.
    orb_payload = None
    locked = date_bars[
        (date_bars["minutes_after_open"] >= 15)
        & (date_bars["orb_high"] > date_bars["orb_low"])
    ]
    if not locked.empty:
        sample = locked.iloc[0]
        orb_payload = {
            "high": float(sample["orb_high"]),
            "low": float(sample["orb_low"]),
        }

    return {
        "date": date_str,
        "bars": bars_payload,
        "decisions": decisions,
        "orb": orb_payload,
        "trend_line": compute_trend_line(bars_payload),
    }
