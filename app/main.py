from __future__ import annotations

from typing import Any

import pandas as pd
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app.analytics.dashboard import build_dashboard_summary
from app.constants import (
    CORS_ALLOW_ORIGINS,
    HEALTH_STATUS_OK,
    MANAGE_POSITION_RESPONSE_NEW_STOP,
    POINT_VALUE,
    POSITION_ACTION_UPDATE_STOP,
    RECENT_BARS_QUERY_LIMIT_DEFAULT,
)
from app.decision_engine import DecisionEngine
from app.decision_engine_v2 import DecisionEngineV2
from app.db.database import Base
from app.db.database import SessionLocal
from app.db.database import engine
from app.db.models_db import CompletedTrade, MarketBar, TradeLog
from app.models.market_state import MarketState
from app.models.trade_signal import TradeSignal
from app.riley_signal_adapter import (
    build_riley_decision_request,
    riley_response_to_trade_signal,
)
from app.slippage import trade_slippage_points_and_dollars

app = FastAPI()
decision_engine = DecisionEngine()
decision_engine_v2 = DecisionEngineV2()

Base.metadata.create_all(bind=engine)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(CORS_ALLOW_ORIGINS),
    allow_credentials=True,
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
        bar = MarketBar(
            timestamp=market.timestamp,
            symbol=market.symbol,
            open=market.open,
            high=market.high,
            low=market.low,
            close=market.close,
            volume=market.volume,
            avg_volume=market.avg_volume,
            vwap=market.vwap,
            atr=market.atr,
            rsi=market.rsi,
            orb_high=market.orb_high,
            orb_low=market.orb_low,
            trend_score=market.trend_score,
            chop_score=market.chop_score,
            minutes_after_open=market.minutes_after_open,
        )

        db.add(bar)
        db.commit()

        recent_bars = decision_engine.get_recent_bars(
            db, market.symbol, limit=RECENT_BARS_QUERY_LIMIT_DEFAULT
        )

        # Use Riley decision engine for early sessions.
        if market.minutes_after_open < decision_engine_v2.config.max_minutes_after_open:
            riley_req = build_riley_decision_request(market, recent_bars)
            riley_resp = decision_engine_v2.decide(riley_req)
            signal = riley_response_to_trade_signal(market, riley_resp)
        else:
            signal = decision_engine.decide(market, recent_bars)

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


@app.post("/signal-v2", response_model=TradeSignal)
def get_signal_v2(market: MarketState) -> TradeSignal:
    db: Session = SessionLocal()

    try:
        bar = MarketBar(
            timestamp=market.timestamp,
            symbol=market.symbol,
            open=market.open,
            high=market.high,
            low=market.low,
            close=market.close,
            volume=market.volume,
            avg_volume=market.avg_volume,
            vwap=market.vwap,
            atr=market.atr,
            rsi=market.rsi,
            orb_high=market.orb_high,
            orb_low=market.orb_low,
            trend_score=market.trend_score,
            chop_score=market.chop_score,
        )

        db.add(bar)
        db.commit()

        recent_bars = decision_engine.get_recent_bars(
            db, market.symbol, limit=RECENT_BARS_QUERY_LIMIT_DEFAULT
        )

        signal = decision_engine.decide(market, recent_bars)

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
    new_stop = decision_engine.adjust_trailing_stop(position)

    return {
        "action": POSITION_ACTION_UPDATE_STOP,
        MANAGE_POSITION_RESPONSE_NEW_STOP: new_stop,
    }


@app.get("/dashboard/summary")
def dashboard_summary() -> dict[str, Any]:
    db: Session = SessionLocal()

    try:
        df = pd.read_sql("SELECT * FROM completed_trades", db.bind)
        return build_dashboard_summary(df)
    finally:
        db.close()
