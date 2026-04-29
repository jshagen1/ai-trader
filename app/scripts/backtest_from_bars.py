from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from app.models.trade_signal import TradeSignal

from app.constants import (
    BACKTEST_MAX_LOOKAHEAD_BARS,
    BACKTEST_RECENT_BARS_WINDOW,
    BACKTEST_SKIP_BARS_AFTER_TRADE,
    POINT_VALUE,
)
from app.decision_engine import DecisionEngine
from app.decision_engine_v2 import DecisionEngineV2
from app.enums import Action, ExitReason, PositionStatus
from app.models.riley.models import RileyDecisionRequest
from app.paths import PROJECT_ROOT, default_trades_db_path
from app.riley_signal_adapter import (
    bar_series_to_candle,
    build_riley_decision_request_from_candles,
    riley_response_to_trade_signal,
)
from app.pnl import calculate_pnl
from app.slippage import (
    apply_entry_slippage,
    apply_exit_slippage,
    trade_slippage_points_and_dollars,
)
from app.time_buckets import time_bucket_label


DB_FILE: Path = default_trades_db_path()


def _bar_calendar_dates(timestamps: pd.Series) -> pd.Series:
    """Calendar date for --today / --history (naive = wall date; aware = local date)."""
    try:
        ts = pd.to_datetime(timestamps, format="mixed", errors="coerce")
    except TypeError:
        ts = timestamps.map(lambda x: pd.to_datetime(x, errors="coerce"))
    if getattr(ts.dtype, "tz", None) is None:
        return ts.dt.normalize().dt.date
    local_tz = datetime.now().astimezone().tzinfo
    if local_tz is None:
        return ts.dt.normalize().dt.date
    return ts.dt.tz_convert(local_tz).dt.date


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backtest using market_bars from SQLite.",
    )
    when = parser.add_mutually_exclusive_group()
    when.add_argument(
        "--today",
        action="store_true",
        help="Only backtest bars from the current local date.",
    )
    when.add_argument(
        "--history",
        action="store_true",
        help="Exclude bars from the current local date (prior days only).",
    )
    parser.add_argument(
        "strategy",
        nargs="?",
        default="auto",
        choices=("auto", "v1", "v2"),
        help=(
            "auto: Riley if minutes_after_open < RileyConfig.max_minutes_after_open, "
            "else ORB (same as POST /signal). v1: ORB only. v2: Riley only. Default: auto."
        ),
    )
    return parser.parse_args(argv)


def build_riley_request(market: Any, bars: pd.DataFrame, index: int) -> RileyDecisionRequest:
    """
    Build the Riley v2 request: candle window ending at the current bar, plus
    context fields used by risk / volatility scoring and pattern detectors.
    """
    start = max(0, index - BACKTEST_RECENT_BARS_WINDOW)
    chunk = bars.iloc[start : index + 1]
    candles = [bar_series_to_candle(row) for _, row in chunk.iterrows()]
    return build_riley_decision_request_from_candles(market, candles)


def to_market_state(row: Any) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=row["symbol"],
        timestamp=row["timestamp"],
        open=row["open"],
        high=row["high"],
        low=row["low"],
        close=row["close"],
        price=row["close"],
        vwap=row["vwap"],
        atr=row["atr"],
        rsi=row["rsi"],
        orb_high=row["orb_high"],
        orb_low=row["orb_low"],
        volume=row["volume"],
        avg_volume=row.get("avg_volume", 0),
        trend_score=row["trend_score"],
        chop_score=row["chop_score"],
        position=PositionStatus.FLAT.value,
        minutes_after_open=int(row["minutes_after_open"]),
    )


def simulate_exit(
    bars: pd.DataFrame,
    entry_index: int,
    signal: TradeSignal,
) -> tuple[float, str, str | None]:
    if signal.entry is None or signal.stop_loss is None or signal.take_profit is None:
        raise ValueError("simulate_exit requires entry, stop_loss, and take_profit")
    entry = signal.entry
    stop = signal.stop_loss
    target = signal.take_profit
    action = signal.action

    future = bars.iloc[entry_index + 1 : entry_index + 1 + BACKTEST_MAX_LOOKAHEAD_BARS]

    for _, row in future.iterrows():
        if action == Action.BUY.value:
            if row["low"] <= stop:
                return stop, ExitReason.STOP.value, row["timestamp"]
            if row["high"] >= target:
                return target, ExitReason.TARGET.value, row["timestamp"]

        if action == Action.SELL.value:
            if row["high"] >= stop:
                return stop, ExitReason.STOP.value, row["timestamp"]
            if row["low"] <= target:
                return target, ExitReason.TARGET.value, row["timestamp"]

    if len(future) == 0:
        return entry, ExitReason.NO_DATA.value, None

    last = future.iloc[-1]
    return last["close"], ExitReason.TIME_EXIT.value, last["timestamp"]


def analyze_results(trades: list[dict[str, Any]]) -> None:
    if not trades:
        print("No trades taken.")
        return

    df = pd.DataFrame(trades)

    print("\nWithout largest winner")
    print("----------------------")

    if len(df) > 1:
        df_without_biggest = df.drop(df["pnl"].idxmax())

        wins2 = df_without_biggest[df_without_biggest["pnl"] > 0]
        losses2 = df_without_biggest[df_without_biggest["pnl"] <= 0]

        gross_profit2 = wins2["pnl"].sum()
        gross_loss2 = abs(losses2["pnl"].sum())
        profit_factor2 = gross_profit2 / gross_loss2 if gross_loss2 else float("inf")

        print(f"Trades: {len(df_without_biggest)}")
        print(f"Net PnL: ${df_without_biggest['pnl'].sum():.2f}")
        print(f"Profit Factor: {profit_factor2:.2f}")
    else:
        print("Not enough trades to remove largest winner.")

    print("\nWithout top 2 largest winners")
    print("------------------------------")

    if len(df) > 2:
        top2_idx = df["pnl"].nlargest(2).index
        df_without_top2 = df.drop(top2_idx)

        wins_top2 = df_without_top2[df_without_top2["pnl"] > 0]
        losses_top2 = df_without_top2[df_without_top2["pnl"] <= 0]

        gross_profit_top2 = wins_top2["pnl"].sum()
        gross_loss_top2 = abs(losses_top2["pnl"].sum())
        profit_factor_top2 = (
            gross_profit_top2 / gross_loss_top2 if gross_loss_top2 else float("inf")
        )

        print(f"Trades: {len(df_without_top2)}")
        print(f"Net PnL: ${df_without_top2['pnl'].sum():.2f}")
        print(f"Profit Factor: {profit_factor_top2:.2f}")

        if "slippage_dollars" in df_without_top2.columns:
            print(f"Slippage: ${df_without_top2['slippage_dollars'].sum():.2f}")

    else:
        print("Not enough trades to remove top 2 largest winners.")

    wins = df[df["pnl"] > 0]
    losses = df[df["pnl"] <= 0]

    total = len(df)
    win_rate = len(wins) / total if total else 0

    avg_win = wins["pnl"].mean() if len(wins) else 0
    avg_loss = abs(losses["pnl"].mean()) if len(losses) else 0

    expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)

    gross_profit = wins["pnl"].sum()
    gross_loss = abs(losses["pnl"].sum())
    profit_factor = gross_profit / gross_loss if gross_loss else float("inf")

    df["equity"] = df["pnl"].cumsum()
    df["running_max"] = df["equity"].cummax()
    df["drawdown"] = df["equity"] - df["running_max"]

    print("\nBacktest Results")
    print("----------------")
    print(f"Trades: {total}")
    print(f"Wins: {len(wins)}")
    print(f"Losses: {len(losses)}")
    print(f"Win Rate: {win_rate:.2%}")
    print(f"Net PnL: ${df['pnl'].sum():.2f}")
    print(f"Avg Win: ${avg_win:.2f}")
    print(f"Avg Loss: ${avg_loss:.2f}")
    print(f"Expectancy: ${expectancy:.2f}")
    print(f"Profit Factor: {profit_factor:.2f}")
    print(f"Max Drawdown: ${df['drawdown'].min():.2f}")

    if "slippage_dollars" in df.columns:
        print("\nSlippage Summary")
        print("----------------")
        print("Avg slippage per trade:", df["slippage_dollars"].mean())
        print("Total slippage:", df["slippage_dollars"].sum())

        print("\nSlippage by Time Bucket")
        print("----------------------")
        print(df.groupby("time_bucket")["slippage_dollars"].mean())

    print("\nBy Strategy")
    print("-----------")
    print(df.groupby("strategy")["pnl"].agg(["count", "sum", "mean", "min", "max"]))

    print("\nBy Time Bucket")
    print("--------------")
    print(
        df.groupby("time_bucket")["pnl"]
        .agg(["count", "sum", "mean", "min", "max"])
    )

    out = PROJECT_ROOT / "backtest_results.csv"
    df.to_csv(out, index=False)
    print(f"\nSaved: {out}")


def main(
    *,
    strategy: str = "auto",
    today: bool = False,
    history: bool = False,
) -> None:
    engine: Engine = create_engine(f"sqlite:///{DB_FILE}")
    decision_engine_v1 = DecisionEngine()
    decision_engine_v2 = DecisionEngineV2()

    bars = pd.read_sql(
        "SELECT * FROM market_bars ORDER BY timestamp ASC",
        engine,
    )

    if bars.empty:
        print("No market_bars found.")
        return

    bars = bars.dropna(subset=[
        "open", "high", "low", "close", "volume",
        "vwap", "atr", "rsi", "orb_high", "orb_low",
        "trend_score", "chop_score",
    ]).reset_index(drop=True)

    if today:
        today_local = date.today()
        mask = _bar_calendar_dates(bars["timestamp"]) == today_local
        bars = bars.loc[mask].reset_index(drop=True)
        print(f"Filtered to local date {today_local}: {len(bars)} bars")
        if bars.empty:
            print("No bars for today.")
            return

    if history:
        today_local = date.today()
        mask = _bar_calendar_dates(bars["timestamp"]) != today_local
        bars = bars.loc[mask].reset_index(drop=True)
        print(f"Excluded local date {today_local}: {len(bars)} bars")
        if bars.empty:
            print("No bars after excluding today.")
            return

    riley_max = decision_engine_v2.config.max_minutes_after_open
    print(f"Loaded {len(bars)} bars")
    if strategy == "auto":
        print(
            f"Strategy: auto (Riley if minutes_after_open < {riley_max}, else ORB)",
        )
    else:
        print(f"Strategy: {strategy} (forced)")
    print("Starting backtest...")

    trades = []
    skip_until_index = -1

    for i in range(len(bars) - BACKTEST_MAX_LOOKAHEAD_BARS):
        if i % 1000 == 0:
            print(f"Processing bar {i}/{len(bars)}")

        if i <= skip_until_index:
            continue

        recent_bars = bars.iloc[max(0, i - BACKTEST_RECENT_BARS_WINDOW) : i].to_dict(
            "records"
        )
        market = to_market_state(bars.iloc[i])

        if strategy == "v1":
            signal = decision_engine_v1.decide(market, recent_bars)
        elif strategy == "v2":
            request = build_riley_request(market, bars, i)
            riley_resp = decision_engine_v2.decide(request)
            signal = riley_response_to_trade_signal(market, riley_resp)
        elif market.minutes_after_open < riley_max:
            request = build_riley_request(market, bars, i)
            riley_resp = decision_engine_v2.decide(request)
            signal = riley_response_to_trade_signal(market, riley_resp)
        else:
            signal = decision_engine_v1.decide(market, recent_bars)

        if signal.action not in (Action.BUY.value, Action.SELL.value):
            continue

        quantity = getattr(signal, "quantity", 1) or 1

        exit_price, exit_reason, exit_time = simulate_exit(bars, i, signal)

        slipped_entry = apply_entry_slippage(signal.action, signal.entry)
        slipped_exit = apply_exit_slippage(signal.action, exit_price)

        pnl = calculate_pnl(signal.action, slipped_entry, slipped_exit, quantity)

        slippage_points, slippage_dollars = trade_slippage_points_and_dollars(
            signal.entry,
            slipped_entry,
            exit_price,
            slipped_exit,
            quantity,
            POINT_VALUE,
        )

        trade = {
            "entry_time": market.timestamp,
            "exit_time": exit_time,
            "symbol": market.symbol,
            "action": signal.action,
            "strategy": signal.strategy,
            "confidence": signal.confidence,
            "entry": signal.entry,
            "stop_loss": signal.stop_loss,
            "take_profit": signal.take_profit,
            "exit_price": exit_price,
            "exit_reason": exit_reason,
            "quantity": quantity,
            "pnl": pnl,
            "slipped_entry": slipped_entry,
            "slipped_exit": slipped_exit,
            "slippage_points": slippage_points,
            "slippage_dollars": slippage_dollars,
            "minutes_after_open": market.minutes_after_open,
            "time_bucket": time_bucket_label(market.minutes_after_open),
            "reason": signal.reason,
        }

        trades.append(trade)

        skip_until_index = i + BACKTEST_SKIP_BARS_AFTER_TRADE

    analyze_results(trades)


if __name__ == "__main__":
    _args = parse_args()
    main(
        strategy=_args.strategy,
        today=_args.today,
        history=_args.history,
    )
