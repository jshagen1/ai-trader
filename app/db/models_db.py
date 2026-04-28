from sqlalchemy import Column, Integer, Float, String, DateTime
from datetime import datetime
from app.db.database import Base


class TradeLog(Base):
    __tablename__ = "trade_logs"

    id = Column(Integer, primary_key=True, index=True)

    timestamp = Column(String)
    symbol = Column(String)

    price = Column(Float)
    vwap = Column(Float)
    atr = Column(Float)
    rsi = Column(Float)

    orb_high = Column(Float)
    orb_low = Column(Float)

    volume = Column(Float)
    avg_volume = Column(Float)

    trend_score = Column(Float)
    chop_score = Column(Float)

    action = Column(String)
    strategy = Column(String)
    confidence = Column(Float)

    reason = Column(String)
    
    entry_price = Column(Float)
    exit_price = Column(Float)
    pnl = Column(Float)
    result = Column(String)
    
class CompletedTrade(Base):
    __tablename__ = "completed_trades"

    id = Column(Integer, primary_key=True, index=True)

    timestamp = Column(String)
    symbol = Column(String)
    action = Column(String)

    entry_price = Column(Float)
    exit_price = Column(Float)
    pnl = Column(Float)
    quantity = Column(Integer)
    
class MarketBar(Base):
    __tablename__ = "market_bars"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(String, index=True)
    symbol = Column(String, index=True)

    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float)
    avg_volume = Column(Float)

    vwap = Column(Float)
    atr = Column(Float)
    rsi = Column(Float)

    orb_high = Column(Float)
    orb_low = Column(Float)

    trend_score = Column(Float)
    chop_score = Column(Float)