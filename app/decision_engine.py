from __future__ import annotations

from enum import Enum
from typing import Any

from sqlalchemy.orm import Session

from app.constants import (
    CHOP_SCORE_MAX,
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
    VWAP_REVERSION_MAX_DISTANCE_ATR,
    VWAP_REVERSION_MAX_UPPER_WICK_RATIO,
    VWAP_REVERSION_MIN_BODY_RATIO,
    VWAP_REVERSION_PULLBACK_LOOKBACK,
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

        signal = self.orb_breakout(market, minutes_after_open, htf_up, htf_down, loss_streak)
        if signal.action != Action.HOLD.value:
            return signal

        # ORB didn't fire — try VWAP reversion as a complementary setup.
        return self.vwap_reversion(market, recent_bars, minutes_after_open, htf_up, loss_streak)

    def orb_breakout(
        self,
        m: Any,
        minutes_after_open: int,
        htf_up: bool,
        htf_down: bool,
        loss_streak: int,
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

    def vwap_reversion(
        self,
        m: Any,
        recent_bars: list[dict[str, Any]],
        minutes_after_open: int,
        htf_up: bool,
        loss_streak: int,
    ) -> TradeSignal:
        """Long-only VWAP reversion: pullback to VWAP in a strong uptrend, then bounce.

        Only fires LONG. Shorts would need a downtrend confirmation that the existing
        bridge-side `trend_score` is biased against producing.
        """
        atr = float(getattr(m, "atr", 0) or 0)
        if atr <= 0:
            return self.hold(Strategy.VWAP_REVERSION, "Invalid ATR for VWAP reversion")

        if not htf_up:
            return self.hold(
                HoldStrategy.HTF_REGIME_FILTER,
                "VWAP reversion long blocked: session not in uptrend",
            )

        lookback = VWAP_REVERSION_PULLBACK_LOOKBACK
        if len(recent_bars) < lookback:
            return self.hold(Strategy.VWAP_REVERSION, "Not enough recent bars")

        cur_ts = str(getattr(m, "timestamp", ""))
        prior = [b for b in recent_bars if str(b.get("timestamp")) != cur_ts][-lookback:]
        if len(prior) < 3:
            return self.hold(Strategy.VWAP_REVERSION, "Not enough prior bars after dedup")

        # Require at least one bar in the window to have CLOSED at or below VWAP.
        # A wick touch is not enough — price must have genuinely pulled back to the level.
        closed_at_vwap = any(float(b["close"]) <= float(b["vwap"]) for b in prior)
        if not closed_at_vwap:
            return self.hold(Strategy.VWAP_REVERSION, "No bar closed at/below VWAP in lookback")

        # The bar immediately before the current bar must still be at the VWAP level (low <= vwap).
        # Ensures we are entering right at the bounce, not bars after the pullback already ended.
        prev_bar = prior[-1]
        if float(prev_bar["low"]) > float(prev_bar["vwap"]):
            return self.hold(Strategy.VWAP_REVERSION, "Prior bar not at VWAP level — bounce already departed")

        # Bounce: current bar is above VWAP, bullish, and not too extended.
        if m.price <= m.vwap:
            return self.hold(Strategy.VWAP_REVERSION, "Price still at/below VWAP")

        if m.close <= m.open:
            return self.hold(Strategy.VWAP_REVERSION, "Current bar not bullish")

        bar_range = max(float(m.high) - float(m.low), 1e-9)
        body = abs(float(m.close) - float(m.open))
        if body / bar_range < VWAP_REVERSION_MIN_BODY_RATIO:
            return self.hold(Strategy.VWAP_REVERSION, "Bounce bar body too small")

        # Large upper wick means price surged but gave back gains — weak buyers.
        upper_wick = float(m.high) - float(m.close)
        if upper_wick / bar_range > VWAP_REVERSION_MAX_UPPER_WICK_RATIO:
            return self.hold(
                Strategy.VWAP_REVERSION,
                f"Bounce bar upper wick too large ({upper_wick:.2f}pts, {upper_wick/bar_range:.0%} of range)",
            )

        # Current bar must close above the prior bar's close — momentum confirmation.
        if float(m.close) <= float(prev_bar["close"]):
            return self.hold(Strategy.VWAP_REVERSION, "Current close does not exceed prior bar close")

        # Volume must be elevated on the bounce bar (committed buyers, not just drift).
        avg_vol = float(getattr(m, "avg_volume", 0) or 0)
        if avg_vol > 0 and float(m.volume) < avg_vol * ORB_VOLUME_SURGE_MULTIPLIER:
            return self.hold(Strategy.VWAP_REVERSION, "Bounce bar volume not elevated")

        if (m.price - m.vwap) > atr * VWAP_REVERSION_MAX_DISTANCE_ATR:
            return self.hold(Strategy.VWAP_REVERSION, "Already extended too far above VWAP")

        risk = atr * STRATEGY_ORB_ATR_MULTIPLIER
        max_risk_points = orb_max_risk_points(minutes_after_open)
        if risk > max_risk_points:
            return self.hold(
                HoldStrategy.RISK_FILTER,
                f"VWAP reversion blocked: stop too wide ({risk:.2f} > {max_risk_points:.2f})",
            )

        quantity = self._sized_quantity(risk, loss_streak)
        live = float(m.price)
        return TradeSignal(
            action=Action.BUY.value,
            strategy=Strategy.VWAP_REVERSION.value,
            confidence=0.75,
            reason=f"VWAP reversion long: pullback bounce in uptrend, qty {quantity}, loss_streak {loss_streak}",
            entry=live,
            stop_loss=live - risk,
            take_profit=live + risk * STRATEGY_ORB_REWARD_RISK_RATIO,
            quantity=quantity,
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
