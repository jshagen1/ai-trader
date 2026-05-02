from __future__ import annotations

from typing import Any

import pandas as pd
from fastapi import Body, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from typing import Annotated
from sqlalchemy.orm import Session

from app.analytics.dashboard import build_dashboard_summary
from app.analytics.session_view import (
    cache_stats as session_cache_stats,
    clear_cache as clear_session_cache,
    list_available_dates,
    session_view,
)
from app.constants import (
    CORS_ALLOW_ORIGINS,
    HEALTH_STATUS_OK,
    MANAGE_POSITION_RESPONSE_NEW_STOP,
    POINT_VALUE,
    POSITION_ACTION_UPDATE_STOP,
    RECENT_BARS_QUERY_LIMIT_DEFAULT,
)
from app.decision_engine import DecisionEngine, should_exit_adverse_close
from app.db.database import Base
from app.db.database import SessionLocal
from app.db.database import engine
from app.db.market_bar_store import upsert_market_bar
from app.db.models_db import CompletedTrade, OrderSkip, TradeLog
from app.loss_streak import get_loss_streak_today
from app.models.market_state import MarketState
from app.models.trade_signal import TradeSignal
from app.signal_guards import guard_signal_against_desync
from app.slippage import trade_slippage_points_and_dollars

app = FastAPI()
decision_engine = DecisionEngine()

Base.metadata.create_all(bind=engine)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(CORS_ALLOW_ORIGINS),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": HEALTH_STATUS_OK}


@app.post("/trade")
def log_trade(data: dict[str, Any]) -> dict[str, str]:
    db = SessionLocal()

    quantity = data.get("quantity") or 1
    signal_entry = data.get("signal_entry", data.get("entry_price"))
    slipped_entry = data.get("slipped_entry", data.get("entry_price"))
    exit_price = data.get("exit_price")
    slipped_exit = data.get("slipped_exit", data.get("exit_price"))

    if None in (signal_entry, slipped_entry, exit_price, slipped_exit):
        slippage_points, slippage_dollars = 0.0, 0.0
    else:
        slippage_points, slippage_dollars = trade_slippage_points_and_dollars(
            float(signal_entry),
            float(slipped_entry),
            float(exit_price),
            float(slipped_exit),
            int(quantity),
            POINT_VALUE,
        )

    trade = CompletedTrade(
        timestamp=data.get("timestamp"),
        symbol=data.get("symbol"),
        action=data.get("action"),
        entry_price=data.get("entry_price"),
        exit_price=data.get("exit_price"),
        pnl=data.get("pnl"),
        quantity=quantity,
        slippage_points=slippage_points,
        slippage_dollars=slippage_dollars,
    )

    db.add(trade)
    db.commit()
    db.close()

    return {"status": HEALTH_STATUS_OK}


@app.post("/signal", response_model=TradeSignal)
def get_signal(market: MarketState) -> TradeSignal:
    db: Session = SessionLocal()


    try:
        upsert_market_bar(db, market)
        db.commit()

        recent_bars = decision_engine.get_recent_bars(
            db, market.symbol, limit=RECENT_BARS_QUERY_LIMIT_DEFAULT
        )
        loss_streak = get_loss_streak_today(db, as_of_iso=market.timestamp)

        signal = decision_engine.decide(market, recent_bars, loss_streak=loss_streak)

        signal = guard_signal_against_desync(market, signal)

        log = TradeLog(
            timestamp=market.timestamp,
            symbol=market.symbol,
            price=market.price,
            vwap=market.vwap,
            atr=market.atr,
            rsi=market.rsi,
            orb_high=market.orb_high,
            orb_low=market.orb_low,
            volume=market.volume,
            avg_volume=market.avg_volume,
            trend_score=market.trend_score,
            chop_score=market.chop_score,
            action=signal.action,
            strategy=signal.strategy,
            confidence=signal.confidence,
            reason=signal.reason,
        )

        db.add(log)
        db.commit()

        return signal

    finally:
        db.close()



@app.post("/manage-position")
def manage_position(position: dict[str, Any]) -> dict[str, Any]:
    # Adverse-close momentum-reversal exit: if the bridge supplies the bars
    # observed since entry under "bars_since_entry", check the rule. When 3
    # consecutive bars have closed against the position, return an exit
    # instruction instead of a trailing-stop update so the bridge can flatten
    # at the current close before the full stop is hit. See EXIT_ADVERSE_CLOSE_STREAK
    # in app/constants.py for the backtest evidence.
    bars_since_entry = position.get("bars_since_entry") or []
    if bars_since_entry and should_exit_adverse_close(
        position.get("action", ""),
        bars_since_entry,
    ):
        return {"action": "EXIT_POSITION", "reason": "ADVERSE_CLOSE"}

    new_stop = decision_engine.adjust_trailing_stop(position)
    return {
        "action": POSITION_ACTION_UPDATE_STOP,
        MANAGE_POSITION_RESPONSE_NEW_STOP: new_stop,
    }


@app.post("/backfill")
def backfill_bars(
    bars: Annotated[list[dict[str, Any]], Body()],
) -> dict[str, Any]:
    """Accept a batch of historical bars from the NinjaTrader bridge and store them.

    Called once per session when the bridge starts up (or reconnects) to sync bars
    that the API missed while offline. Safe to call multiple times — the underlying
    INSERT OR IGNORE discards any bar whose (symbol, timestamp) already exists.

    Body must be a JSON array matching the MarketState field set.
    Only active-session bars should be sent (bridge filters pre-market / overnight).
    """
    db: Session = SessionLocal()
    try:
        stored = 0
        for raw in bars:
            try:
                market = MarketState(
                    symbol=raw["symbol"],
                    timestamp=raw["timestamp"],
                    open=float(raw["open"]),
                    high=float(raw["high"]),
                    low=float(raw["low"]),
                    close=float(raw["close"]),
                    price=float(raw["price"]),
                    vwap=float(raw["vwap"]),
                    atr=float(raw["atr"]),
                    rsi=float(raw["rsi"]),
                    orb_high=float(raw["orb_high"]),
                    orb_low=float(raw["orb_low"]),
                    volume=float(raw["volume"]),
                    avg_volume=float(raw.get("avg_volume", 0) or 0),
                    trend_score=float(raw["trend_score"]),
                    chop_score=float(raw["chop_score"]),
                    position=str(raw.get("position", "flat")),
                    minutes_after_open=int(raw["minutes_after_open"]),
                )
                upsert_market_bar(db, market)
                stored += 1
            except (KeyError, TypeError, ValueError):
                # Skip malformed bars rather than aborting the whole batch.
                continue
        db.commit()
        return {"status": HEALTH_STATUS_OK, "bars_stored": stored, "bars_received": len(bars)}
    finally:
        db.close()


@app.post("/skip")
def log_order_skip(data: dict[str, Any]) -> dict[str, str]:
    """Bridge calls this when it skips an order client-side (e.g. signal-vs-live drift)."""
    db: Session = SessionLocal()

    try:
        skip = OrderSkip(
            timestamp=data.get("timestamp"),
            symbol=data.get("symbol"),
            action=data.get("action"),
            signal_entry=data.get("signal_entry"),
            live_quote=data.get("live_quote"),
            drift_points=data.get("drift_points"),
            reason=data.get("reason"),
        )
        db.add(skip)
        db.commit()
        return {"status": HEALTH_STATUS_OK}
    finally:
        db.close()


@app.get("/dashboard/sessions")
def dashboard_sessions() -> dict[str, Any]:
    db: Session = SessionLocal()
    try:
        return {"dates": list_available_dates(db)}
    finally:
        db.close()


@app.get("/dashboard/sessions/{date_str}")
def dashboard_session(date_str: str) -> dict[str, Any]:
    db: Session = SessionLocal()
    try:
        return session_view(db, date_str)
    finally:
        db.close()


@app.get("/dashboard/cache")
def dashboard_cache_stats() -> dict[str, Any]:
    """Visibility into the session_view cache: size + hit/miss counters."""
    return session_cache_stats()


@app.post("/dashboard/cache/clear")
def dashboard_cache_clear() -> dict[str, str]:
    """Manually drop the cache (e.g. after editing strategy constants without restart)."""
    clear_session_cache()
    return {"status": HEALTH_STATUS_OK}


@app.get("/dashboard/summary")
def dashboard_summary() -> dict[str, Any]:
    db: Session = SessionLocal()

    try:
        trades_df = pd.read_sql("SELECT * FROM completed_trades", db.bind)
        skips_df = pd.read_sql(
            "SELECT * FROM order_skips ORDER BY id DESC", db.bind
        )
        return build_dashboard_summary(trades_df, skips_df)
    finally:
        db.close()
