from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

import joblib
import pandas as pd
from sqlalchemy.orm import Session

from app.constants import (
    CHOP_SCORE_MAX,
    MINUTES_AFTER_OPEN_ORB_CONTEXT,
    ML_PROBABILITY_DEFAULT_NO_MODEL,
    ORB_LONG_ML_THRESHOLD,
    ORB_LONG_RSI_HIGH,
    ORB_LONG_RSI_LOW,
    ORB_LONG_SCORE_THRESHOLD,
    ORB_LONG_WEIGHT_ABOVE_VWAP,
    ORB_LONG_WEIGHT_BREAK_ORB,
    ORB_LONG_WEIGHT_RSI,
    ORB_LONG_WEIGHT_TIME,
    ORB_LONG_WEIGHT_TREND,
    ORB_LONG_WEIGHT_VOLUME,
    ORB_SHORT_ML_THRESHOLD,
    ORB_SHORT_RSI_HIGH,
    ORB_SHORT_RSI_LOW,
    ORB_SHORT_SCORE_THRESHOLD,
    ORB_SHORT_WEIGHT_BELOW_VWAP,
    ORB_SHORT_WEIGHT_BREAK_ORB,
    ORB_SHORT_WEIGHT_RSI,
    ORB_SHORT_WEIGHT_TIME,
    ORB_SHORT_WEIGHT_TREND,
    ORB_SHORT_WEIGHT_VOLUME,
    ORB_TREND_SCORE_FOR_WEIGHT,
    ORB_VOLUME_SURGE_MULTIPLIER,
    POINT_VALUE,
    POSITION_KEY_ACTION,
    POSITION_KEY_ATR,
    POSITION_KEY_CURRENT_PRICE,
    POSITION_KEY_ENTRY_PRICE,
    POSITION_KEY_INITIAL_STOP,
    POSITION_KEY_STOP_LOSS,
    QUANTITY_MAX_CONTRACTS_DEFAULT,
    QUANTITY_MAX_RISK_DOLLARS_DEFAULT,
    RECENT_BARS_QUERY_LIMIT_DEFAULT,
    SESSION_WEAK_MID_A_END,
    SESSION_WEAK_MID_A_START,
    SESSION_WEAK_MID_B_END,
    SESSION_WEAK_MID_B_START,
    SESSION_ORB_NO_ENTRY_MINUTES_AFTER_OPEN,
    SESSION_WEAK_OPENING_END,
    SESSION_WEAK_OPENING_START,
    STRATEGY_ORB_ATR_MULTIPLIER,
    STRATEGY_ORB_REWARD_RISK_RATIO,
    TRAIL_ATR_TIGHTEN_MULTIPLIER,
    TRAIL_LOCK_FRACTION_OF_INITIAL,
    TRAIL_PROFIT_MULTIPLIER_TIER_1,
    TRAIL_PROFIT_MULTIPLIER_TIER_2,
    TRAIL_PROFIT_MULTIPLIER_TIER_3,
    TREND_SCORE_MIN,
    orb_max_risk_points,
)
from app.enums import Action, HoldStrategy, PositionStatus, Strategy
from app.machine_learning.features import build_features
from app.market_data import bar_dict_from_orm
from app.market_time import parse_minutes_after_open
from app.models.trade_signal import TradeSignal


class DecisionEngine:
    def __init__(self) -> None:
        model_path = Path(__file__).resolve().parent / "machine_learning" / "model.pkl"
        self.model: Any = joblib.load(model_path) if model_path.exists() else None

    def decide(
        self,
        market: Any,
        recent_bars: list[dict[str, Any]] | None = None,
    ) -> TradeSignal:
        recent_bars = list(recent_bars or [])

        minutes_after_open = parse_minutes_after_open(
            getattr(market, "minutes_after_open", None)
        )
        if minutes_after_open is None:
            return self.hold(
                HoldStrategy.TIME_FILTER,
                "Invalid minutes after open",
            )

        if market.position != PositionStatus.FLAT.value:
            return self.hold(HoldStrategy.POSITION_FILTER, "Already in position")

        if minutes_after_open >= SESSION_ORB_NO_ENTRY_MINUTES_AFTER_OPEN:
            return self.hold(
                HoldStrategy.TIME_FILTER,
                f"No ORB entries at or after {SESSION_ORB_NO_ENTRY_MINUTES_AFTER_OPEN} "
                f"min after open (now {minutes_after_open})",
            )

        if SESSION_WEAK_OPENING_START <= minutes_after_open < SESSION_WEAK_OPENING_END:
            return self.hold(
                HoldStrategy.TIME_FILTER,
                f"Blocked weak opening window: {minutes_after_open}",
            )

        if SESSION_WEAK_MID_A_START <= minutes_after_open < SESSION_WEAK_MID_A_END:
            return self.hold(
                HoldStrategy.TIME_FILTER,
                f"Blocked weak mid-session: {minutes_after_open}",
            )

        if SESSION_WEAK_MID_B_START <= minutes_after_open < SESSION_WEAK_MID_B_END:
            return self.hold(
                HoldStrategy.TIME_FILTER,
                f"Blocked weak mid-session: {minutes_after_open}",
            )

        if market.chop_score >= CHOP_SCORE_MAX:
            return self.hold(
                HoldStrategy.CHOP_FILTER,
                f"Chop too high: {market.chop_score:.2f}",
            )

        if market.atr <= 0:
            return self.hold(HoldStrategy.ATR_FILTER, "Invalid ATR")

        if market.trend_score < TREND_SCORE_MIN:
            return self.hold(
                HoldStrategy.TREND_FILTER,
                f"Blocked: trend score too weak ({market.trend_score:.2f})",
            )

        return self.orb_breakout(market, minutes_after_open)

    def orb_breakout(self, m: Any, minutes_after_open: int) -> TradeSignal:
        risk = m.atr * STRATEGY_ORB_ATR_MULTIPLIER

        max_risk_points = orb_max_risk_points(minutes_after_open)
        quantity = self.calculate_quantity(risk)

        if risk > max_risk_points:
            return self.hold(
                HoldStrategy.RISK_FILTER,
                f"Blocked: stop too wide. Risk={risk:.2f} points (max {max_risk_points:.2f})",
            )

        if risk <= 0:
            return self.hold(Strategy.ORB_BREAKOUT, "Invalid ATR")

        ml_prob = self.ml_probability(m)

        long_score = 0.0
        if m.price > m.orb_high:
            long_score += ORB_LONG_WEIGHT_BREAK_ORB
        if m.price > m.vwap:
            long_score += ORB_LONG_WEIGHT_ABOVE_VWAP
        if m.trend_score >= ORB_TREND_SCORE_FOR_WEIGHT:
            long_score += ORB_LONG_WEIGHT_TREND
        if m.avg_volume and m.volume > m.avg_volume * ORB_VOLUME_SURGE_MULTIPLIER:
            long_score += ORB_LONG_WEIGHT_VOLUME
        if ORB_LONG_RSI_LOW <= m.rsi <= ORB_LONG_RSI_HIGH:
            long_score += ORB_LONG_WEIGHT_RSI
        if m.minutes_after_open >= MINUTES_AFTER_OPEN_ORB_CONTEXT:
            long_score += ORB_LONG_WEIGHT_TIME

        short_score = 0.0
        if m.price < m.orb_low:
            short_score += ORB_SHORT_WEIGHT_BREAK_ORB
        if m.price < m.vwap:
            short_score += ORB_SHORT_WEIGHT_BELOW_VWAP
        if m.trend_score >= ORB_TREND_SCORE_FOR_WEIGHT:
            short_score += ORB_SHORT_WEIGHT_TREND
        if m.avg_volume and m.volume > m.avg_volume * ORB_VOLUME_SURGE_MULTIPLIER:
            short_score += ORB_SHORT_WEIGHT_VOLUME
        if ORB_SHORT_RSI_LOW <= m.rsi <= ORB_SHORT_RSI_HIGH:
            short_score += ORB_SHORT_WEIGHT_RSI
        if m.minutes_after_open >= MINUTES_AFTER_OPEN_ORB_CONTEXT:
            short_score += ORB_SHORT_WEIGHT_TIME

        if long_score >= ORB_LONG_SCORE_THRESHOLD and ml_prob >= ORB_LONG_ML_THRESHOLD:
            return TradeSignal(
                action=Action.BUY.value,
                strategy=Strategy.ORB_BREAKOUT.value,
                confidence=round((long_score + ml_prob) / 2, 2),
                reason=(
                    f"ORB long score {long_score:.2f}, "
                    f"ML probability {ml_prob:.2f}, qty {quantity}"
                ),
                entry=m.price,
                stop_loss=m.price - risk,
                take_profit=m.price + risk * STRATEGY_ORB_REWARD_RISK_RATIO,
                quantity=quantity,
            )

        if short_score >= ORB_SHORT_SCORE_THRESHOLD and ml_prob <= ORB_SHORT_ML_THRESHOLD:
            return TradeSignal(
                action=Action.SELL.value,
                strategy=Strategy.ORB_BREAKOUT.value,
                confidence=round((short_score + (1 - ml_prob)) / 2, 2),
                reason=(
                    f"ORB short score {short_score:.2f}, "
                    f"ML probability {ml_prob:.2f}, qty {quantity}"
                ),
                entry=m.price,
                stop_loss=m.price + risk,
                take_profit=m.price - risk * STRATEGY_ORB_REWARD_RISK_RATIO,
                quantity=quantity,
            )

        return TradeSignal(
            action=Action.HOLD.value,
            strategy=Strategy.ORB_BREAKOUT.value,
            confidence=round(max(long_score, short_score), 2),
            reason=(
                f"No ORB setup. "
                f"Long score {long_score:.2f}, "
                f"Short score {short_score:.2f}, "
                f"ML probability {ml_prob:.2f}"
            ),
            entry=None,
            stop_loss=None,
            take_profit=None,
        )

    def ml_probability(self, market: Any) -> float:
        if self.model is None:
            return ML_PROBABILITY_DEFAULT_NO_MODEL

        row = {
            "open": market.open,
            "high": market.high,
            "low": market.low,
            "close": market.close,
            "vwap": market.vwap,
            "orb_high": market.orb_high,
            "orb_low": market.orb_low,
            "atr": market.atr,
            "rsi": market.rsi,
            "volume": market.volume,
            "avg_volume": market.avg_volume,
            "trend_score": market.trend_score,
            "chop_score": market.chop_score,
        }

        X = pd.DataFrame([build_features(row)])
        return float(self.model.predict_proba(X)[0][1])

    def get_recent_bars(
        self,
        db: Session,
        symbol: str,
        limit: int = RECENT_BARS_QUERY_LIMIT_DEFAULT,
    ) -> list[dict[str, Any]]:
        from app.db.models_db import MarketBar

        rows = (
            db.query(MarketBar)
            .filter(MarketBar.symbol == symbol)
            .order_by(MarketBar.id.desc())
            .limit(limit)
            .all()
        )

        rows = list(reversed(rows))
        return [bar_dict_from_orm(row) for row in rows]

    def adjust_trailing_stop(self, position: dict[str, Any]) -> float:
        entry = position[POSITION_KEY_ENTRY_PRICE]
        current = position[POSITION_KEY_CURRENT_PRICE]
        stop = position[POSITION_KEY_STOP_LOSS]
        action = position[POSITION_KEY_ACTION]
        atr = position[POSITION_KEY_ATR]

        if action == Action.BUY.value:
            initial_risk = entry - position[POSITION_KEY_INITIAL_STOP]
            profit = current - entry

            if profit >= initial_risk * TRAIL_PROFIT_MULTIPLIER_TIER_3:
                return max(stop, current - atr * TRAIL_ATR_TIGHTEN_MULTIPLIER)

            if profit >= initial_risk * TRAIL_PROFIT_MULTIPLIER_TIER_2:
                return max(stop, entry + initial_risk * TRAIL_LOCK_FRACTION_OF_INITIAL)

            if profit >= initial_risk * TRAIL_PROFIT_MULTIPLIER_TIER_1:
                return max(stop, entry)

        if action == Action.SELL.value:
            initial_risk = position[POSITION_KEY_INITIAL_STOP] - entry
            profit = entry - current

            if profit >= initial_risk * TRAIL_PROFIT_MULTIPLIER_TIER_3:
                return min(stop, current + atr * TRAIL_ATR_TIGHTEN_MULTIPLIER)

            if profit >= initial_risk * TRAIL_PROFIT_MULTIPLIER_TIER_2:
                return min(stop, entry - initial_risk * TRAIL_LOCK_FRACTION_OF_INITIAL)

            if profit >= initial_risk * TRAIL_PROFIT_MULTIPLIER_TIER_1:
                return min(stop, entry)

        return stop

    def hold(self, strategy: HoldStrategy | Strategy, reason: str) -> TradeSignal:
        label = strategy.value if isinstance(strategy, Enum) else str(strategy)
        return TradeSignal(
            action=Action.HOLD.value,
            strategy=label,
            confidence=0.0,
            reason=reason,
            entry=None,
            stop_loss=None,
            take_profit=None,
            quantity=1,
        )

    def calculate_quantity(
        self,
        risk_points: float,
        *,
        point_value: float = POINT_VALUE,
        max_risk_dollars: float = QUANTITY_MAX_RISK_DOLLARS_DEFAULT,
        max_contracts: int = QUANTITY_MAX_CONTRACTS_DEFAULT,
    ) -> int:
        if risk_points <= 0:
            return 1

        quantity = int(max_risk_dollars / (risk_points * point_value))
        return max(1, min(quantity, max_contracts))
