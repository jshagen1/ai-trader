import pandas as pd
from fastapi import FastAPI
from app.models.market_state import MarketState
from app.models.trade_signal import TradeSignal
from app.decision_engine import DecisionEngine
from app.db.database import Base
from app.db.database import engine
from app.db.database import SessionLocal
from app.db.models_db import TradeLog, CompletedTrade, MarketBar
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()
decision_engine = DecisionEngine()

Base.metadata.create_all(bind=engine)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/trade")
def log_trade(data: dict):
    db = SessionLocal()

    trade = CompletedTrade(
        timestamp=data.get("timestamp"),
        symbol=data.get("symbol"),
        action=data.get("action"),
        entry_price=data.get("entry_price"),
        exit_price=data.get("exit_price"),
        pnl=data.get("pnl"),
        quantity=data.get("quantity"),
    )

    db.add(trade)
    db.commit()
    db.close()

    return {"status": "ok"}

@app.post("/signal", response_model=TradeSignal)
def get_signal(market: MarketState):
    db = SessionLocal()

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

        recent_bars = decision_engine.get_recent_bars(db, market.symbol, limit=50)

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
            reason=signal.reason
        )
        
        db.add(log)
        db.commit()

        return signal

    finally:
        db.close()

@app.post("/manage-position")
def manage_position(position: dict):
    new_stop = decision_engine.adjust_trailing_stop(position)

    return {
        "action": "UPDATE_STOP",
        "new_stop_loss": new_stop
    }


@app.get("/dashboard/summary")
def dashboard_summary():
    db = SessionLocal()

    try:
        df = pd.read_sql("SELECT * FROM completed_trades", db.bind)

        if df.empty:
            return {
                "total_trades": 0,
                "win_rate": 0,
                "net_pnl": 0,
                "expectancy": 0,
                "profit_factor": 0,
                "max_drawdown": 0,
                "equity_curve": [],
                "recent_trades": [],
            }

        wins = df[df["pnl"] > 0]
        losses = df[df["pnl"] <= 0]

        total = len(df)
        win_rate = len(wins) / total if total else 0

        avg_win = wins["pnl"].mean() if len(wins) else 0
        avg_loss = abs(losses["pnl"].mean()) if len(losses) else 0
        loss_rate = 1 - win_rate

        expectancy = (win_rate * avg_win) - (loss_rate * avg_loss)

        gross_profit = wins["pnl"].sum()
        gross_loss = abs(losses["pnl"].sum())
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

        df["equity"] = df["pnl"].cumsum()
        df["running_max"] = df["equity"].cummax()
        df["drawdown"] = df["equity"] - df["running_max"]

        return {
            "total_trades": total,
            "win_rate": round(win_rate * 100, 2),
            "net_pnl": round(df["pnl"].sum(), 2),
            "expectancy": round(expectancy, 2),
            "profit_factor": round(profit_factor, 2),
            "max_drawdown": round(df["drawdown"].min(), 2),
            "equity_curve": [
                {"trade": i + 1, "equity": round(row["equity"], 2)}
                for i, row in df.iterrows()
            ],
            "recent_trades": df.tail(20).to_dict(orient="records"),
        }

    finally:
        db.close()