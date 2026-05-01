"""Post-decision checks: reject live orders when the bridge payload is internally inconsistent."""

from __future__ import annotations

from app.constants import DESYNC_PRICE_GUARD_MAX_POINTS
from app.enums import Action, HoldStrategy
from app.models.market_state import MarketState
from app.models.trade_signal import TradeSignal


def guard_signal_against_desync(market: MarketState, signal: TradeSignal) -> TradeSignal:
    """If BUY/SELL, require ``price`` (live quote from bridge) aligned with ``close`` and with ``entry``."""
    if signal.action not in (Action.BUY.value, Action.SELL.value):
        return signal
    if signal.entry is None or signal.stop_loss is None or signal.take_profit is None:
        return signal

    live = float(market.price)
    close = float(market.close)
    gap = abs(live - close)
    if gap > DESYNC_PRICE_GUARD_MAX_POINTS:
        return TradeSignal(
            action=Action.HOLD.value,
            strategy=HoldStrategy.DESYNC_PROTECTION.value,
            confidence=0.0,
            reason=(
                f"Blocked: price vs close gap {gap:.2f} pts (max {DESYNC_PRICE_GUARD_MAX_POINTS}); "
                "fix bridge payload or bar series (Realtime, BarsInProgress)."
            ),
            entry=None,
            stop_loss=None,
            take_profit=None,
            quantity=1,
        )

    entry = float(signal.entry)
    if abs(entry - live) > DESYNC_PRICE_GUARD_MAX_POINTS:
        return TradeSignal(
            action=Action.HOLD.value,
            strategy=HoldStrategy.DESYNC_PROTECTION.value,
            confidence=0.0,
            reason=(
                f"Blocked: entry {entry} too far from live price {live} "
                f"(>{DESYNC_PRICE_GUARD_MAX_POINTS} pts)"
            ),
            entry=None,
            stop_loss=None,
            take_profit=None,
            quantity=1,
        )

    return signal
