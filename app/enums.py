from enum import Enum


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class Strategy(str, Enum):
    ORB_BREAKOUT = "ORB_BREAKOUT"
    NONE = "NONE"


class HoldStrategy(str, Enum):
    """Strategy label on HOLD signals (filters / validation)."""

    TIME_FILTER = "TIME_FILTER"
    POSITION_FILTER = "POSITION_FILTER"
    CHOP_FILTER = "CHOP_FILTER"
    ATR_FILTER = "ATR_FILTER"
    TREND_FILTER = "TREND_FILTER"
    RISK_FILTER = "RISK_FILTER"
    DESYNC_PROTECTION = "DESYNC_PROTECTION"
    HTF_REGIME_FILTER = "HTF_REGIME_FILTER"
    LOSS_STREAK_HALT = "LOSS_STREAK_HALT"


class PositionStatus(str, Enum):
    FLAT = "flat"


class ExitReason(str, Enum):
    STOP = "STOP"
    TARGET = "TARGET"
    TIME_EXIT = "TIME_EXIT"
    NO_DATA = "NO_DATA"
    ADVERSE_CLOSE = "ADVERSE_CLOSE"
