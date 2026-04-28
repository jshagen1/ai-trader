from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class MarketState:
    symbol: str
    timestamp: str
    price: float
    vwap: float
    atr: float
    rsi: float
    orb_high: float
    orb_low: float
    volume: float
    avg_volume: float
    trend_score: float
    chop_score: float
    position: str
    minutes_after_open: int
    open: float
    high: float
    low: float
    close: float
