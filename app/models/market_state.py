from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class MarketState:
    """Inbound bar from the NinjaTrader bridge.

    For execution, ``price`` must match the same reference the chart uses for orders
    (typically last / close of the signal bar). ``close`` should match ``price`` on
    a consistent bar; large gaps trigger DESYNC_PROTECTION in ``signal_guards``.
    """

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
