from __future__ import annotations

from enum import Enum
from typing import Any

from sqlalchemy.orm import Session

from app.constants import (
    CHOP_SCORE_MAX,
    EXIT_ADVERSE_CLOSE_STREAK,
    HTF_SLOPE_LOOKBACK,
    LOSS_STREAK_HALT_THRESHOLD,
    LOSS_STREAK_REDUCE_QTY_THRESHOLD,
    MINUTES_AFTER_OPEN_ORB_CONTEXT,
    ORB_LONG_RSI_HIGH,
    ORB_LONG_RSI_LOW,
    ORB_LONG_SCORE_THRESHOLD,
    ORB_LONG_WEIGHT_ABOVE_VWAP,
    ORB_LONG_WEIGHT_BREAK_ORB,
    ORB_LONG_WEIGHT_RSI,
    ORB_LONG_WEIGHT_TIME,
    ORB_LONG_WEIGHT_TREND,
    ORB_LONG_WEIGHT_VOLUME,
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
    ORB_ENTRY_MAX_WICK_ATR,
    ORB_ENTRY_PROXIMITY_ATR,
    ORB_RSI_EXHAUSTION_LOOKBACK,
    QUANTITY_MAX_CONTRACTS_DEFAULT,
    QUANTITY_MAX_RISK_DOLLARS_DEFAULT,
    RECENT_BARS_QUERY_LIMIT_DEFAULT,
    SESSION_WEAK_MID_A_END,
    SESSION_WEAK_MID_A_START,
    SESSION_ORB_NO_ENTRY_MINUTES_AFTER_OPEN,
    SESSION_WEAK_OPENING_END,
    SESSION_WEAK_OPENING_START,
    STRATEGY_ORB_ATR_MULTIPLIER,
    STRATEGY_ORB_EARLY_ATR_MULTIPLIER,
    STRATEGY_ORB_EARLY_SESSION_MINUTES,
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
from app.htf_regime import htf_regime_adaptive
from app.market_data import bar_dict_from_orm
from app.market_time import parse_minutes_after_open
from app.models.trade_signal import TradeSignal


class DecisionEngine:
    def decide(
        self,
        market: Any,
        recent_bars: list[dict[str, Any]] | None = None,
        loss_streak: int = 0,
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

        if loss_streak >= LOSS_STREAK_HALT_THRESHOLD:
            return self.hold(
                HoldStrategy.LOSS_STREAK_HALT,
                f"Halted: {loss_streak} consecutive losses today (>= {LOSS_STREAK_HALT_THRESHOLD})",
            )

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

        # Adaptive regime check: uses EMA20 early in the session (30–60 bars) and
        # graduates to EMA50 once enough history exists (60+ bars). This ensures the
        # ORB breakout window at 8:45 CT is not blocked solely due to EMA50 cold-start.
        # See htf_regime.htf_regime_adaptive for the full graduation logic.
        htf_up, htf_down, _htf_period = htf_regime_adaptive(recent_bars, HTF_SLOPE_LOOKBACK)

        return self.orb_breakout(market, minutes_after_open, htf_up, htf_down, loss_streak, recent_bars)

    def orb_breakout(
        self,
        m: Any,
        minutes_after_open: int,
        htf_up: bool,
        htf_down: bool,
        loss_streak: int,
        recent_bars: list[dict[str, Any]] | None = None,
    ) -> TradeSignal:
        # Wider stop for the first 30 min: opening range bars spike further and
        # a 1.25x ATR stop gets hit before the breakout extends.
        atr_mult = (
            STRATEGY_ORB_EARLY_ATR_MULTIPLIER
            if minutes_after_open < STRATEGY_ORB_EARLY_SESSION_MINUTES
            else STRATEGY_ORB_ATR_MULTIPLIER
        )
        risk = m.atr * atr_mult

        max_risk_points = orb_max_risk_points(minutes_after_open)
        quantity = self._sized_quantity(risk, loss_streak)

        if risk > max_risk_points:
            return self.hold(
                HoldStrategy.RISK_FILTER,
                f"Blocked: stop too wide. Risk={risk:.2f} points (max {max_risk_points:.2f})",
            )

        if risk <= 0:
            return self.hold(Strategy.ORB_BREAKOUT, "Invalid ATR")

        # Require price to be at or above the ORB high (longs) / at or below the
        # ORB low (shorts). A tiny ATR-based tolerance allows bars that tested the
        # level intrabar and closed within 1-2 ticks below it (ORB_ENTRY_PROXIMITY_ATR
        # = 0.15 ≈ half a tick of tolerance at typical ATR). Bars whose close is
        # clearly inside the range are blocked — they have not broken out.
        tolerance = m.atr * ORB_ENTRY_PROXIMITY_ATR
        broke_high = m.price >= m.orb_high - tolerance
        broke_low = m.price <= m.orb_low + tolerance
        if not broke_high and not broke_low:
            return self.hold(
                HoldStrategy.TREND_FILTER,
                f"No ORB breakout: price {m.price:.2f} not at ORB level "
                f"[{m.orb_low:.2f}–{m.orb_high:.2f}] (tolerance {tolerance:.2f})",
            )

        # RSI exhaustion guard: if RSI was overbought (long) or oversold (short)
        # within the recent lookback, the move is already exhausted. Re-entry of
        # RSI into the valid range from an extreme signals fading momentum, not
        # a fresh breakout.
        lookback_bars = (recent_bars or [])[-ORB_RSI_EXHAUSTION_LOOKBACK:]
        recent_rsi = [b["rsi"] for b in lookback_bars if b.get("rsi") is not None]
        if broke_high and any(r > ORB_LONG_RSI_HIGH for r in recent_rsi):
            return self.hold(
                HoldStrategy.TREND_FILTER,
                f"RSI exhaustion: long RSI was overbought (>{ORB_LONG_RSI_HIGH}) "
                f"within last {ORB_RSI_EXHAUSTION_LOOKBACK} bars",
            )
        if broke_low and any(r < ORB_SHORT_RSI_LOW for r in recent_rsi):
            return self.hold(
                HoldStrategy.TREND_FILTER,
                f"RSI exhaustion: short RSI was oversold (<{ORB_SHORT_RSI_LOW}) "
                f"within last {ORB_RSI_EXHAUSTION_LOOKBACK} bars",
            )

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

        if long_score >= ORB_LONG_SCORE_THRESHOLD:
            if not (ORB_LONG_RSI_LOW <= m.rsi <= ORB_LONG_RSI_HIGH):
                return self.hold(
                    HoldStrategy.TREND_FILTER,
                    f"Blocked: RSI {m.rsi:.1f} outside long range [{ORB_LONG_RSI_LOW}-{ORB_LONG_RSI_HIGH}]",
                )
            if not htf_up:
                return self.hold(
                    HoldStrategy.HTF_REGIME_FILTER,
                    "Blocked long: session EMA not in uptrend",
                )
            # Skip entries where the bar already spiked deep into the stop zone:
            # a large downward wick signals a stop-hunt during the entry candle.
            wick_below = float(m.close) - float(m.low)
            if wick_below > m.atr * ORB_ENTRY_MAX_WICK_ATR:
                return self.hold(
                    HoldStrategy.RISK_FILTER,
                    f"ORB long blocked: entry bar wick {wick_below:.2f}pts"
                    f" > {m.atr * ORB_ENTRY_MAX_WICK_ATR:.2f}pts ({ORB_ENTRY_MAX_WICK_ATR}x ATR)",
                )
            live = float(m.price)
            return TradeSignal(
                action=Action.BUY.value,
                strategy=Strategy.ORB_BREAKOUT.value,
                confidence=round(long_score, 2),
                reason=f"ORB long score {long_score:.2f}, qty {quantity}, loss_streak {loss_streak}",
                entry=live,
                stop_loss=live - risk,
                take_profit=live + risk * STRATEGY_ORB_REWARD_RISK_RATIO,
                quantity=quantity,
            )

        if short_score >= ORB_SHORT_SCORE_THRESHOLD:
            if not (ORB_SHORT_RSI_LOW <= m.rsi <= ORB_SHORT_RSI_HIGH):
                return self.hold(
                    HoldStrategy.TREND_FILTER,
                    f"Blocked: RSI {m.rsi:.1f} outside short range [{ORB_SHORT_RSI_LOW}-{ORB_SHORT_RSI_HIGH}]",
                )
            if not htf_down:
                return self.hold(
                    HoldStrategy.HTF_REGIME_FILTER,
                    "Blocked short: session EMA not in downtrend",
                )
            # Skip entries where the bar already spiked deep into the stop zone:
            # a large upward wick signals a stop-hunt during the entry candle.
            wick_above = float(m.high) - float(m.close)
            if wick_above > m.atr * ORB_ENTRY_MAX_WICK_ATR:
                return self.hold(
                    HoldStrategy.RISK_FILTER,
                    f"ORB short blocked: entry bar wick {wick_above:.2f}pts"
                    f" > {m.atr * ORB_ENTRY_MAX_WICK_ATR:.2f}pts ({ORB_ENTRY_MAX_WICK_ATR}x ATR)",
                )
            live = float(m.price)
            return TradeSignal(
                action=Action.SELL.value,
                strategy=Strategy.ORB_BREAKOUT.value,
                confidence=round(short_score, 2),
                reason=f"ORB short score {short_score:.2f}, qty {quantity}, loss_streak {loss_streak}",
                entry=live,
                stop_loss=live + risk,
                take_profit=live - risk * STRATEGY_ORB_REWARD_RISK_RATIO,
                quantity=quantity,
            )

        return TradeSignal(
            action=Action.HOLD.value,
            strategy=Strategy.ORB_BREAKOUT.value,
            confidence=round(max(long_score, short_score), 2),
            reason=(
                f"No ORB setup. Long score {long_score:.2f}, Short score {short_score:.2f}"
            ),
            entry=None,
            stop_loss=None,
            take_profit=None,
        )

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

    def _sized_quantity(self, risk_points: float, loss_streak: int) -> int:
        base = self.calculate_quantity(risk_points)
        if loss_streak >= LOSS_STREAK_REDUCE_QTY_THRESHOLD:
            return 1
        return base

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


def should_exit_adverse_close(
    action: str,
    bars_after_entry: list[dict[str, Any]],
    streak: int = EXIT_ADVERSE_CLOSE_STREAK,
) -> bool:
    """True when the most recent `streak` bars all closed against the position
    direction (close < open for BUY, close > open for SELL).

    Used as a momentum-reversal exit: a long that's seen 3 reds in a row has
    almost certainly given up its move, so cut at the current close rather than
    riding to the full stop. Backtest evidence and rationale documented at
    EXIT_ADVERSE_CLOSE_STREAK in app/constants.py.
    """
    if streak <= 0 or len(bars_after_entry) < streak:
        return False
    recent = bars_after_entry[-streak:]
    is_buy = action == Action.BUY.value
    for bar in recent:
        o = bar.get("open")
        c = bar.get("close")
        if o is None or c is None:
            return False
        went_against = (c < o) if is_buy else (c > o)
        if not went_against:
            return False
    return True
