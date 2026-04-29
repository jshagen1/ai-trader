from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


Direction = Literal["BULLISH", "BEARISH"]
Action = Literal["BUY", "SELL", "HOLD"]


class Candle(BaseModel):
    timestamp: Optional[str] = None
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float] = None

    @property
    def range(self) -> float:
        return max(0.0, self.high - self.low)

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def upper_wick(self) -> float:
        return max(0.0, self.high - max(self.open, self.close))

    @property
    def lower_wick(self) -> float:
        return max(0.0, min(self.open, self.close) - self.low)


class PatternSignal(BaseModel):
    name: str
    direction: Direction
    score: float = Field(ge=0.0, le=1.0)
    reason: str


class RileyDecisionRequest(BaseModel):
    symbol: str
    candles: List[Candle]

    # Optional context from NinjaTrader or the API data pipeline.
    support_level: Optional[float] = None
    resistance_level: Optional[float] = None
    atr_pct: Optional[float] = None
    spread_pct: Optional[float] = None
    minutes_since_open: Optional[int] = None
    loss_streak: Optional[int] = 0

    # Useful for A/B testing.
    ab_test_group: Optional[str] = "riley"


class RileyDecisionResponse(BaseModel):
    strategy: str
    action: Action
    confidence: float = Field(ge=0.0, le=1.0)
    position_size_multiplier: float = Field(ge=0.0, le=1.0)
    detected_patterns: List[PatternSignal]
    reasons: List[str]
