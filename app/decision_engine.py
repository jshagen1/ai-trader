from pathlib import Path
import pandas as pd
import joblib
import math

from app.models.trade_signal import TradeSignal
from app.machine_learning.features import build_features


class DecisionEngine:
    def __init__(self):
        model_path = Path(__file__).resolve().parent / "machine_learning" / "model.pkl"
        self.model = joblib.load(model_path) if model_path.exists() else None

    def decide(self, market, recent_bars=None) -> TradeSignal:
        recent_bars = recent_bars or []
        
        # Handle NaN values in minutes_after_open
        raw_minutes = getattr(market, "minutes_after_open", 999)
        try:
            minutes_after_open = float(raw_minutes)
        except (TypeError, ValueError):
            return self.hold("TIME_FILTER", "Invalid minutes after open")

        if math.isnan(minutes_after_open):
            return self.hold("TIME_FILTER", "Invalid minutes after open")

        minutes_after_open = int(minutes_after_open)

        if market.position != "flat":
            return self.hold("POSITION_FILTER", "Already in position")
        
        if 30 <= minutes_after_open < 60:
            return self.hold(
                "TIME_FILTER",
                f"Blocked weak opening window: {minutes_after_open}"
            )

        if market.chop_score >= 0.55:
            return self.hold("CHOP_FILTER", f"Chop too high: {market.chop_score:.2f}")

        if market.atr <= 0:
            return self.hold("ATR_FILTER", "Invalid ATR")
        
        if market.trend_score < 0.65:
            return self.hold(
                "TREND_FILTER",
                f"Blocked: trend score too weak ({market.trend_score:.2f})"
            )

        return self.orb_breakout(market)

    def orb_breakout(self, m) -> TradeSignal:
        risk = m.atr * 1.25
        
        quantity = self.calculate_quantity(risk)
        
        max_risk_points = 7.5
        if risk > max_risk_points:
            return self.hold(
                "RISK_FILTER",
                f"Blocked: stop too wide. Risk={risk:.2f} points"
            )

        if risk <= 0:
            return self.hold("ORB_BREAKOUT", "Invalid ATR")

        ml_prob = self.ml_probability(m)

        long_score = 0.0

        if m.price > m.orb_high:
            long_score += 0.30

        if m.price > m.vwap:
            long_score += 0.20

        if m.trend_score >= 0.7:
            long_score += 0.20

        if m.avg_volume and m.volume > m.avg_volume * 1.20:
            long_score += 0.15

        if 55 <= m.rsi <= 72:
            long_score += 0.10

        if m.minutes_after_open >= 15:
            long_score += 0.05

        short_score = 0.0

        if m.price < m.orb_low:
            short_score += 0.30

        if m.price < m.vwap:
            short_score += 0.20

        if m.trend_score >= 0.7:
            short_score += 0.20

        if m.avg_volume and m.volume > m.avg_volume * 1.20:
            short_score += 0.15

        if 28 <= m.rsi <= 45:
            short_score += 0.10

        if m.minutes_after_open >= 15:
            short_score += 0.05

        if long_score >= 0.70 and ml_prob >= 0.55:
            return TradeSignal(
                action="BUY",
                strategy="ORB_BREAKOUT",
                confidence=round((long_score + ml_prob) / 2, 2),
                entry=m.price,
                stop_loss=m.price - risk,
                take_profit=m.price + risk * 2.0,
                quantity=quantity,
                reason=f"ORB long score {long_score:.2f}, ML probability {ml_prob:.2f}, qty {quantity}",
            )

        if short_score >= 0.70 and ml_prob <= 0.45:
            return TradeSignal(
                action="SELL",
                strategy="ORB_BREAKOUT",
                confidence=round((short_score + (1 - ml_prob)) / 2, 2),
                entry=m.price,
                stop_loss=m.price + risk,
                take_profit=m.price - risk * 2.0,
                quantity=quantity,
                reason=f"ORB short score {short_score:.2f}, ML probability {ml_prob:.2f}, qty {quantity}",
            )

        return TradeSignal(
            action="HOLD",
            strategy="ORB_BREAKOUT",
            confidence=round(max(long_score, short_score), 2),
            entry=None,
            stop_loss=None,
            take_profit=None,
            reason=(
                f"No ORB setup. "
                f"Long score {long_score:.2f}, "
                f"Short score {short_score:.2f}, "
                f"ML probability {ml_prob:.2f}"
            ),
        )

    def ml_probability(self, market):
        if self.model is None:
            return 0.5

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

    def get_recent_bars(self, db, symbol: str, limit: int = 50):
        from app.db.models_db import MarketBar

        rows = (
            db.query(MarketBar)
            .filter(MarketBar.symbol == symbol)
            .order_by(MarketBar.id.desc())
            .limit(limit)
            .all()
        )

        rows = list(reversed(rows))

        return [
            {
                "timestamp": row.timestamp,
                "open": row.open,
                "high": row.high,
                "low": row.low,
                "close": row.close,
                "volume": row.volume,
                "avg_volume": row.avg_volume,
                "vwap": row.vwap,
                "atr": row.atr,
                "rsi": row.rsi,
                "orb_high": row.orb_high,
                "orb_low": row.orb_low,
                "trend_score": row.trend_score,
                "chop_score": row.chop_score,
            }
            for row in rows
        ]

    def adjust_trailing_stop(self, position: dict):
        entry = position["entry_price"]
        current = position["current_price"]
        stop = position["stop_loss"]
        action = position["action"]
        atr = position["atr"]

        if action == "BUY":
            initial_risk = entry - position["initial_stop"]
            profit = current - entry

            if profit >= initial_risk * 2:
                return max(stop, current - atr * 0.75)

            if profit >= initial_risk * 1.5:
                return max(stop, entry + initial_risk * 0.5)

            if profit >= initial_risk:
                return max(stop, entry)

        if action == "SELL":
            initial_risk = position["initial_stop"] - entry
            profit = entry - current

            if profit >= initial_risk * 2:
                return min(stop, current + atr * 0.75)

            if profit >= initial_risk * 1.5:
                return min(stop, entry - initial_risk * 0.5)

            if profit >= initial_risk:
                return min(stop, entry)

        return stop

    def hold(self, strategy, reason):
        return TradeSignal(
            action="HOLD",
            strategy=strategy,
            confidence=0,
            entry=None,
            stop_loss=None,
            take_profit=None,
            reason=reason,
        )
        
    def calculate_quantity(self, risk_points, point_value=50.0, max_risk_dollars=100.0, max_contracts=3):
        if risk_points <= 0:
            return 1

        quantity = int(max_risk_dollars / (risk_points * point_value))

        return max(1, min(quantity, max_contracts))
