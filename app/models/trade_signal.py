from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class TradeSignal:
    action: str
    strategy: str
    confidence: float
    reason: str
    entry: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    quantity: int = 1
