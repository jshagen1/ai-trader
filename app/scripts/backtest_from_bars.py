from pathlib import Path
from types import SimpleNamespace
import math

import pandas as pd
from sqlalchemy import create_engine

from app.constants import POINT_VALUE
from app.decision_engine import DecisionEngine
from app.slippage import trade_slippage_points_and_dollars


ROOT = Path(__file__).resolve().parents[2]
DB_FILE = ROOT / "trades.db"

MAX_LOOKAHEAD_BARS = 20

SLIPPAGE_TICKS = 1
TICK_SIZE = 0.25
SLIPPAGE_POINTS = SLIPPAGE_TICKS * TICK_SIZE

def get_time_bucket(minutes):
    try:
        minutes = float(minutes)
    except (TypeError, ValueError):
        return "INVALID"

    if math.isnan(minutes):
        return "INVALID"

    minutes = int(minutes)

    # Early session
    if minutes < 30:
        return "0-30"
    if minutes < 60:
        return "30-60"
    if minutes < 90:
        return "60-90"
    if minutes < 120:
        return "90-120"

    # Mid session
    if minutes < 150:
        return "120-150"
    if minutes < 180:
        return "150-180"

    # Late session (THIS is the important split)
    if minutes < 240:
        return "180-240"
    if minutes < 300:
        return "240-300"
    if minutes < 390:
        return "300-390"

    return "390+"

def apply_entry_slippage(action, entry):
    if action == "BUY":
        return entry + SLIPPAGE_POINTS
    if action == "SELL":
        return entry - SLIPPAGE_POINTS
    return entry


def apply_exit_slippage(action, exit_price):
    if action == "BUY":
        return exit_price - SLIPPAGE_POINTS
    if action == "SELL":
        return exit_price + SLIPPAGE_POINTS
    return exit_price


def to_market_state(row):
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
        position="flat",
        minutes_after_open=row.get("minutes_after_open", 999),
    )


def simulate_exit(bars, entry_index, signal):
    entry = signal.entry
    stop = signal.stop_loss
    target = signal.take_profit
    action = signal.action

    future = bars.iloc[entry_index + 1:entry_index + 1 + MAX_LOOKAHEAD_BARS]

    for _, row in future.iterrows():
        if action == "BUY":
            if row["low"] <= stop:
                return stop, "STOP", row["timestamp"]
            if row["high"] >= target:
                return target, "TARGET", row["timestamp"]

        if action == "SELL":
            if row["high"] >= stop:
                return stop, "STOP", row["timestamp"]
            if row["low"] <= target:
                return target, "TARGET", row["timestamp"]

    if len(future) == 0:
        return entry, "NO_DATA", None

    last = future.iloc[-1]
    return last["close"], "TIME_EXIT", last["timestamp"]


def calculate_pnl(action, entry, exit_price, quantity=1):
    if action == "BUY":
        return (exit_price - entry) * POINT_VALUE * quantity

    if action == "SELL":
        return (entry - exit_price) * POINT_VALUE * quantity

    return 0


def analyze_results(trades):
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

    out = ROOT / "backtest_results.csv"
    df.to_csv(out, index=False)
    print(f"\nSaved: {out}")


def main():
    engine = create_engine(f"sqlite:///{DB_FILE}")
    decision_engine = DecisionEngine()

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
    
    bars = bars.dropna(subset=["minutes_after_open"]).reset_index(drop=True)

    print(f"Loaded {len(bars)} bars")
    print("Starting backtest...")

    trades = []
    skip_until_index = -1

    for i in range(len(bars) - MAX_LOOKAHEAD_BARS):
        if i % 1000 == 0:
            print(f"Processing bar {i}/{len(bars)}")

        if i <= skip_until_index:
            continue

        recent_bars = bars.iloc[max(0, i - 50):i].to_dict("records")
        market = to_market_state(bars.iloc[i])

        signal = decision_engine.decide(market, recent_bars)

        if signal.action not in ["BUY", "SELL"]:
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
            "time_bucket": get_time_bucket(getattr(market, "minutes_after_open", None)),
            "reason": signal.reason,
        }

        trades.append(trade)

        skip_until_index = i + 5

    analyze_results(trades)


if __name__ == "__main__":
    main()
