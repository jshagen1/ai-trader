from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from app.models.riley.config import RileyConfig
from app.models.riley.models import (
    Candle,
    PatternSignal,
    RileyDecisionRequest,
    RileyDecisionResponse,
)


@dataclass(frozen=True)
class DecisionEngineV2:
    """
    Standalone Riley decision engine.

    This engine is intentionally separate from the ORB breakout strategy so both
    strategies can coexist for A/B testing without sharing decision logic.

    Strategy concept:
    - Prefer reversal/trap setups after extended directional movement.
    - Require evidence of momentum loss or a large rejection/reversal candle.
    - Treat exact textbook pattern shape as less important than context,
      velocity, rejection, and nearby support/resistance.
    """

    config: RileyConfig = RileyConfig()

    def decide(self, request: RileyDecisionRequest) -> RileyDecisionResponse:
        candles = request.candles

        if len(candles) < self.config.min_candles_required:
            return RileyDecisionResponse(
                strategy="riley",
                action="HOLD",
                confidence=0.0,
                position_size_multiplier=0.0,
                detected_patterns=[],
                reasons=[f"Need at least {self.config.min_candles_required} candles."],
            )

        # Hard session window — must run before pattern scoring and cannot be
        # overridden by later BUY/SELL assignment (previous bug: set HOLD then overwrote).
        if request.minutes_since_open is not None:
            mo = int(request.minutes_since_open)
            if mo < self.config.min_minutes_after_open:
                return RileyDecisionResponse(
                    strategy="riley",
                    action="HOLD",
                    confidence=0.0,
                    position_size_multiplier=0.0,
                    detected_patterns=[],
                    reasons=[
                        f"Before minimum session time ({mo} min after open; "
                        f"need >= {self.config.min_minutes_after_open} min).",
                    ],
                )
            if mo > self.config.max_minutes_after_open:
                return RileyDecisionResponse(
                    strategy="riley",
                    action="HOLD",
                    confidence=0.0,
                    position_size_multiplier=0.0,
                    detected_patterns=[],
                    reasons=[
                        f"After maximum session time ({mo} min after open; "
                        f"max {self.config.max_minutes_after_open} min).",
                    ],
                )

        support = request.support_level
        resistance = request.resistance_level

        signals: List[PatternSignal] = []
        signals.extend(self._detect_three_line_strike(candles))
        signals.extend(self._detect_trap(candles, support, resistance))
        signals.extend(self._detect_reversal_flag(candles, support, resistance))
        signals.extend(self._detect_head_and_shoulders_like_reversal(candles, support, resistance))

        bullish_score = sum(s.score for s in signals if s.direction == "BULLISH")
        bearish_score = sum(s.score for s in signals if s.direction == "BEARISH")

        risk_score = self._risk_score(request)
        volatility_penalty = self._volatility_penalty(request)

        bullish_score = max(0.0, bullish_score - risk_score - volatility_penalty)
        bearish_score = max(0.0, bearish_score - risk_score - volatility_penalty)

        if bullish_score >= self.config.entry_threshold and bullish_score > bearish_score:
            action = "BUY"
            confidence = min(0.95, bullish_score)
        elif bearish_score >= self.config.entry_threshold and bearish_score > bullish_score:
            action = "SELL"
            confidence = min(0.95, bearish_score)
        else:
            action = "HOLD"
            confidence = max(bullish_score, bearish_score)

        position_size_multiplier = self._position_size_multiplier(confidence, risk_score)

        reasons = self._build_reasons(signals, risk_score, volatility_penalty, action)

        return RileyDecisionResponse(
            strategy="riley",
            action=action,
            confidence=round(confidence, 4),
            position_size_multiplier=round(position_size_multiplier, 4),
            detected_patterns=signals,
            reasons=reasons,
        )

    def _detect_three_line_strike(self, candles: List[Candle]) -> List[PatternSignal]:
        """
        Detects a large reversal candle that gives back the movement of the
        prior 3-5 candles. It is only scored if the context shows prior
        directional movement, because this pattern should not be used alone.
        """
        signals: List[PatternSignal] = []

        for lookback in range(3, 6):
            if len(candles) < lookback + 1:
                continue

            prior = candles[-lookback - 1 : -1]
            reversal = candles[-1]

            prior_bullish = all(c.close > c.open for c in prior)
            prior_bearish = all(c.close < c.open for c in prior)

            prior_move = abs(prior[-1].close - prior[0].open)
            reversal_body = abs(reversal.close - reversal.open)
            avg_body = self._average_body(candles[-lookback - 1 : -1])

            if prior_move <= 0 or avg_body <= 0:
                continue

            # Bearish three line strike: prior push up, then large red reversal.
            if (
                prior_bullish
                and reversal.close < reversal.open
                and reversal_body >= avg_body * self.config.large_reversal_body_multiplier
                and reversal.close <= prior[0].open
            ):
                signals.append(
                    PatternSignal(
                        name="bearish_three_line_strike",
                        direction="BEARISH",
                        score=0.72,
                        reason="Large bearish reversal candle gave back the prior 3-5 candle advance.",
                    )
                )

            # Bullish three line strike: prior push down, then large green reversal.
            if (
                prior_bearish
                and reversal.close > reversal.open
                and reversal_body >= avg_body * self.config.large_reversal_body_multiplier
                and reversal.close >= prior[0].open
            ):
                signals.append(
                    PatternSignal(
                        name="bullish_three_line_strike",
                        direction="BULLISH",
                        score=0.72,
                        reason="Large bullish reversal candle gave back the prior 3-5 candle decline.",
                    )
                )

        return signals

    def _detect_trap(
        self,
        candles: List[Candle],
        support: Optional[float],
        resistance: Optional[float],
    ) -> List[PatternSignal]:
        """
        Trap logic:
        - Breakout move should be unusually strong for the recent trend.
        - Reversal should happen quickly after the breakout.
        - The reversal velocity matters more than volume.
        """
        if len(candles) < 8:
            return []

        signals: List[PatternSignal] = []
        recent = candles[-8:]
        last = candles[-1]
        prev = candles[-2]
        avg_range = self._average_range(recent[:-2])

        if avg_range <= 0:
            return []

        # Bull trap / bearish trap near resistance.
        broke_above_resistance = (
            resistance is not None
            and prev.high > resistance
            and prev.range >= avg_range * self.config.trap_breakout_range_multiplier
        )

        fast_bearish_reversal = (
            last.close < resistance if resistance is not None else False
        ) and last.close < last.open and last.range >= avg_range * self.config.trap_reversal_range_multiplier

        if broke_above_resistance and fast_bearish_reversal:
            signals.append(
                PatternSignal(
                    name="bearish_trap",
                    direction="BEARISH",
                    score=0.84,
                    reason="Strong breakout above resistance quickly failed back below the level.",
                )
            )

        # Bear trap / bullish trap near support.
        broke_below_support = (
            support is not None
            and prev.low < support
            and prev.range >= avg_range * self.config.trap_breakout_range_multiplier
        )

        fast_bullish_reversal = (
            last.close > support if support is not None else False
        ) and last.close > last.open and last.range >= avg_range * self.config.trap_reversal_range_multiplier

        if broke_below_support and fast_bullish_reversal:
            signals.append(
                PatternSignal(
                    name="bullish_trap",
                    direction="BULLISH",
                    score=0.84,
                    reason="Strong breakdown below support quickly failed back above the level.",
                )
            )

        return signals

    def _detect_reversal_flag(
        self,
        candles: List[Candle],
        support: Optional[float],
        resistance: Optional[float],
    ) -> List[PatternSignal]:
        """
        Reversal flag approximation:
        - Strong directional move.
        - Then sideways/choppy compression showing momentum loss.
        - Better when it occurs near a major support/resistance zone.
        """
        if len(candles) < 12:
            return []

        impulse = candles[-12:-6]
        consolidation = candles[-6:]
        impulse_move = impulse[-1].close - impulse[0].open
        impulse_range = sum(c.range for c in impulse)
        consolidation_range = max(c.high for c in consolidation) - min(c.low for c in consolidation)

        if impulse_range <= 0:
            return []

        compression_ratio = consolidation_range / impulse_range
        near_resistance = resistance is not None and abs(consolidation[-1].close - resistance) / consolidation[-1].close <= self.config.near_level_pct
        near_support = support is not None and abs(consolidation[-1].close - support) / consolidation[-1].close <= self.config.near_level_pct

        signals: List[PatternSignal] = []

        if impulse_move > 0 and compression_ratio <= self.config.max_consolidation_to_impulse_ratio and near_resistance:
            signals.append(
                PatternSignal(
                    name="bearish_reversal_flag",
                    direction="BEARISH",
                    score=0.76,
                    reason="Strong move into resistance shifted into sideways compression, suggesting momentum loss.",
                )
            )

        if impulse_move < 0 and compression_ratio <= self.config.max_consolidation_to_impulse_ratio and near_support:
            signals.append(
                PatternSignal(
                    name="bullish_reversal_flag",
                    direction="BULLISH",
                    score=0.76,
                    reason="Strong move into support shifted into sideways compression, suggesting momentum loss.",
                )
            )

        return signals

    def _detect_head_and_shoulders_like_reversal(
        self,
        candles: List[Candle],
        support: Optional[float],
        resistance: Optional[float],
    ) -> List[PatternSignal]:
        """
        Loose head-and-shoulders style reversal:
        - Looks for a large rejection after an extended move.
        - Does not require a flat neckline or perfect symmetry.
        """
        if len(candles) < 10:
            return []

        recent = candles[-10:]
        last = candles[-1]
        avg_range = self._average_range(recent[:-1])

        if avg_range <= 0:
            return []

        upper_rejection = last.upper_wick >= avg_range * self.config.rejection_wick_multiplier
        lower_rejection = last.lower_wick >= avg_range * self.config.rejection_wick_multiplier

        prior_move = recent[-2].close - recent[0].open

        signals: List[PatternSignal] = []

        if prior_move > 0 and upper_rejection and (resistance is None or last.high >= resistance * (1 - self.config.near_level_pct)):
            signals.append(
                PatternSignal(
                    name="bearish_head_and_shoulders_like_reversal",
                    direction="BEARISH",
                    score=0.68,
                    reason="Extended bullish move showed larger-than-normal rejection near resistance.",
                )
            )

        if prior_move < 0 and lower_rejection and (support is None or last.low <= support * (1 + self.config.near_level_pct)):
            signals.append(
                PatternSignal(
                    name="bullish_head_and_shoulders_like_reversal",
                    direction="BULLISH",
                    score=0.68,
                    reason="Extended bearish move showed larger-than-normal rejection near support.",
                )
            )

        return signals

    def _risk_score(self, request: RileyDecisionRequest) -> float:
        score = 0.0

        if request.spread_pct is not None and request.spread_pct > self.config.max_spread_pct:
            score += 0.2

        if request.loss_streak is not None and request.loss_streak >= self.config.max_loss_streak_before_penalty:
            score += 0.25

        return min(0.5, score)

    def _volatility_penalty(self, request: RileyDecisionRequest) -> float:
        if request.atr_pct is None:
            return 0.0

        if request.atr_pct > self.config.max_atr_pct:
            return 0.25

        return 0.0

    def _position_size_multiplier(self, confidence: float, risk_score: float) -> float:
        if confidence < self.config.entry_threshold:
            return 0.0

        return max(0.0, min(1.0, confidence - risk_score))

    def _build_reasons(
        self,
        signals: List[PatternSignal],
        risk_score: float,
        volatility_penalty: float,
        action: str,
    ) -> List[str]:
        reasons = [s.reason for s in signals]

        if risk_score > 0:
            reasons.append(f"Risk penalty applied: {risk_score:.2f}.")

        if volatility_penalty > 0:
            reasons.append(f"Volatility penalty applied: {volatility_penalty:.2f}.")

        if action == "HOLD" and not reasons:
            reasons.append("No Riley reversal, trap, or momentum-loss pattern detected.")

        if action == "HOLD" and reasons:
            reasons.append("Pattern context was not strong enough to clear entry threshold.")

        return reasons

    @staticmethod
    def _average_body(candles: List[Candle]) -> float:
        return sum(abs(c.close - c.open) for c in candles) / max(1, len(candles))

    @staticmethod
    def _average_range(candles: List[Candle]) -> float:
        return sum(c.range for c in candles) / max(1, len(candles))
