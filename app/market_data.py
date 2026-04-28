"""Normalize ORM / DB rows into bar dicts used by the decision engine."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.db.models_db import MarketBar


def bar_dict_from_orm(row: MarketBar) -> dict[str, Any]:
    """Map a `MarketBar` ORM instance to the dict shape `DecisionEngine.get_recent_bars` returns."""
    return {
        "timestamp": row.timestamp,
        "open": row.open,
        "high": row.high,
        "low": row.low,
        "close": row.close,
        "volume": row.volume,
        "avg_volume": row.avg_volume,
        "vwap": row.vwap,
        "atr": row.atr,
        "rsi": row.rsi,
        "orb_high": row.orb_high,
        "orb_low": row.orb_low,
        "trend_score": row.trend_score,
        "chop_score": row.chop_score,
    }
