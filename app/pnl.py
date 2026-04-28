from __future__ import annotations

from app.constants import POINT_VALUE
from app.enums import Action


def calculate_pnl(
    action: str,
    entry: float,
    exit_price: float,
    quantity: int = 1,
    *,
    point_value: float = POINT_VALUE,
) -> float:
    q = quantity if quantity else 1
    if action == Action.BUY.value:
        return (exit_price - entry) * point_value * q
    if action == Action.SELL.value:
        return (entry - exit_price) * point_value * q
    return 0.0
