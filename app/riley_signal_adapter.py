"""Build RileyDecisionRequest from API/backtest context; map Riley response to TradeSignal."""

from __future__ import annotations

from typing import Any, Mapping

import pandas as pd

from app.constants import (
    QUANTITY_MAX_CONTRACTS_DEFAULT,
    STRATEGY_ORB_ATR_MULTIPLIER,
    STRATEGY_ORB_REWARD_RISK_RATIO,
)
from app.enums import Action
from app.models.riley.models import Candle, RileyDecisionRequest, RileyDecisionResponse
from app.models.trade_signal import TradeSignal


def bar_dict_to_candle(d: Mapping[str, Any]) -> Candle:
    ts = d.get("timestamp")
    ts_out: str | None = None
    if ts is not None and not pd.isna(ts):
        ts_out = str(ts)
    vol = d.get("volume")
    vol_f: float | None = None
    if vol is not None and not pd.isna(vol):
        vol_f = float(vol)
    return Candle(
        timestamp=ts_out,
        open=float(d["open"]),
        high=float(d["high"]),
        low=float(d["low"]),
        close=float(d["close"]),
        volume=vol_f,
    )


def bar_series_to_candle(row: pd.Series) -> Candle:
    return bar_dict_to_candle(row.to_dict())


def candle_from_market(market: Any) -> Candle:
    vol = getattr(market, "volume", None)
    vol_f: float | None = None
    if vol is not None and not pd.isna(vol):
        vol_f = float(vol)
    return Candle(
        timestamp=str(market.timestamp),
        open=float(market.open),
        high=float(market.high),
        low=float(market.low),
        close=float(market.close),
        volume=vol_f,
    )


def build_riley_decision_request_from_candles(
    market: Any,
    candles: list[Candle],
) -> RileyDecisionRequest:
    price = float(market.price)
    atr = float(getattr(market, "atr", 0) or 0)
    atr_pct = (atr / price) if price > 0 else None

    mo = getattr(market, "minutes_after_open", None)
    try:
        minutes_since_open = int(mo) if mo is not None and not pd.isna(mo) else None
    except (TypeError, ValueError):
        minutes_since_open = None

    return RileyDecisionRequest(
        symbol=str(market.symbol),
        candles=candles,
        support_level=float(market.orb_low),
        resistance_level=float(market.orb_high),
        atr_pct=atr_pct,
        spread_pct=None,
        minutes_since_open=minutes_since_open,
        loss_streak=0,
        ab_test_group="riley",
    )


def build_riley_decision_request(
    market: Any,
    recent_bars: list[dict[str, Any]],
) -> RileyDecisionRequest:
    """
    DB history (oldest→newest) plus current bar from `market`.
    Drops a trailing DB row if it matches the current timestamp, then appends
    the live OHLC from `market` so the last candle matches the request payload.
    """
    cur_ts = str(market.timestamp)
    rows = list(recent_bars)
    if rows and str(rows[-1].get("timestamp")) == cur_ts:
        rows = rows[:-1]
    candles = [bar_dict_to_candle(d) for d in rows]
    candles.append(candle_from_market(market))
    return build_riley_decision_request_from_candles(market, candles)


def riley_response_to_trade_signal(
    market: Any,
    resp: RileyDecisionResponse,
) -> TradeSignal:
    """Map Riley output to TradeSignal (same sizing geometry as ORB for the bridge)."""
    reason = "; ".join(resp.reasons) if resp.reasons else resp.strategy

    if resp.action not in (Action.BUY.value, Action.SELL.value):
        return TradeSignal(
            action=Action.HOLD.value,
            strategy=resp.strategy,
            confidence=resp.confidence,
            reason=reason,
        )

    atr = float(getattr(market, "atr", 0) or 0)
    risk = atr * STRATEGY_ORB_ATR_MULTIPLIER
    if risk <= 0:
        return TradeSignal(
            action=Action.HOLD.value,
            strategy=resp.strategy,
            confidence=resp.confidence,
            reason=f"{reason} [cannot size risk — ATR invalid]",
        )

    mult = float(resp.position_size_multiplier or 0.0)
    quantity = max(
        1,
        min(
            QUANTITY_MAX_CONTRACTS_DEFAULT,
            int(round(1 + mult * (QUANTITY_MAX_CONTRACTS_DEFAULT - 1))),
        ),
    )

    price = float(market.price)
    if resp.action == Action.BUY.value:
        return TradeSignal(
            action=Action.BUY.value,
            strategy=resp.strategy,
            confidence=resp.confidence,
            reason=reason,
            entry=price,
            stop_loss=price - risk,
            take_profit=price + risk * STRATEGY_ORB_REWARD_RISK_RATIO,
            quantity=quantity,
        )
    return TradeSignal(
        action=Action.SELL.value,
        strategy=resp.strategy,
        confidence=resp.confidence,
        reason=reason,
        entry=price,
        stop_loss=price + risk,
        take_profit=price - risk * STRATEGY_ORB_REWARD_RISK_RATIO,
        quantity=quantity,
    )
