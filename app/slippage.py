from __future__ import annotations

from app.constants import SLIPPAGE_POINTS_PER_SIDE
from app.enums import Action


def trade_slippage_points_and_dollars(
    signal_entry: float,
    slipped_entry: float,
    exit_price: float,
    slipped_exit: float,
    quantity: int,
    point_value: float,
) -> tuple[float, float]:
    q = quantity if quantity else 1
    slippage_points = abs(slipped_entry - signal_entry) + abs(exit_price - slipped_exit)
    slippage_dollars = slippage_points * point_value * q
    return slippage_points, slippage_dollars


def apply_entry_slippage(
    action: str,
    entry: float,
    slip: float = SLIPPAGE_POINTS_PER_SIDE,
) -> float:
    if action == Action.BUY.value:
        return entry + slip
    if action == Action.SELL.value:
        return entry - slip
    return entry


def apply_exit_slippage(
    action: str,
    exit_price: float,
    slip: float = SLIPPAGE_POINTS_PER_SIDE,
) -> float:
    if action == Action.BUY.value:
        return exit_price - slip
    if action == Action.SELL.value:
        return exit_price + slip
    return exit_price
