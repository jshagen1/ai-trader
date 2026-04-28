from enum import Enum

class Action(Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"

class Strategy(Enum):
    VWAP_REVERSION = "VWAP_REVERSION"
    ORB_BREAKOUT = "ORB_BREAKOUT"
    NONE = "NONE"
