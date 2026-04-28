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
