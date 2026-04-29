from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RileyConfig:
    min_candles_required: int = 10
    entry_threshold: float = 0.80

    large_reversal_body_multiplier: float = 1.75
    rejection_wick_multiplier: float = 0.90
    trap_breakout_range_multiplier: float = 1.60
    trap_reversal_range_multiplier: float = 1.40
    max_consolidation_to_impulse_ratio: float = 0.30

    near_level_pct: float = 0.001

    max_spread_pct: float = 0.08
    max_atr_pct: float = 0.010
    min_minutes_after_open: int = 10
    max_minutes_after_open: int = 90
    max_loss_streak_before_penalty: int = 1