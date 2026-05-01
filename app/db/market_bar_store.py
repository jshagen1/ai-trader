"""Persist incoming bars; duplicate (symbol, timestamp) rows are skipped (SQLite INSERT OR IGNORE)."""

from __future__ import annotations

from sqlalchemy import insert
from sqlalchemy.orm import Session

from app.db.models_db import MarketBar
from app.models.market_state import MarketState


def upsert_market_bar(db: Session, market: MarketState) -> None:
    """Insert one bar; if (symbol, timestamp) already exists, do nothing (requires unique index)."""
    stmt = (
        insert(MarketBar.__table__)
        .prefix_with("OR IGNORE")
        .values(
            timestamp=market.timestamp,
            symbol=market.symbol,
            open=market.open,
            high=market.high,
            low=market.low,
            close=market.close,
            volume=market.volume,
            avg_volume=market.avg_volume,
            vwap=market.vwap,
            atr=market.atr,
            rsi=market.rsi,
            orb_high=market.orb_high,
            orb_low=market.orb_low,
            trend_score=market.trend_score,
            chop_score=market.chop_score,
            minutes_after_open=market.minutes_after_open,
        )
    )
    db.execute(stmt)
