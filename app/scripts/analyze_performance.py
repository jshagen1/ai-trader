from pathlib import Path
from sqlalchemy import create_engine
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_FILE = PROJECT_ROOT / "trades.db"
DB_PATH = f"sqlite:///{DB_FILE}"
MES_POINT_VALUE = 5.0


def calculate_pnl(action, entry, exit_price, quantity):
    if action == "BUY":
        return (exit_price - entry) * POINT_VALUE * quantity

    if action == "SELL":
        return (entry - exit_price) * POINT_VALUE * quantity

    return 0


def analyze():
    engine = create_engine(DB_PATH)

    df = pd.read_sql("SELECT * FROM completed_trades", engine)

    if df.empty:
        print("No logs found.")
        return

    # Keep only actual trades
    trades = df.copy()

    if trades.empty:
        print("No BUY/SELL trades found yet.")
        return

    # Calculate PnL if not already populated
    if "pnl" not in trades.columns:
        trades["pnl"] = trades.apply(calculate_pnl, axis=1)
    else:
        trades["pnl"] = trades["pnl"].fillna(trades.apply(calculate_pnl, axis=1))

    trades = trades.dropna(subset=["pnl"])

    if trades.empty:
        print("Trades exist, but no completed trades with entry_price and exit_price yet.")
        return

    wins = trades[trades["pnl"] > 0]
    losses = trades[trades["pnl"] <= 0]

    total_trades = len(trades)
    win_count = len(wins)
    loss_count = len(losses)

    win_rate = win_count / total_trades
    loss_rate = loss_count / total_trades

    avg_win = wins["pnl"].mean() if win_count else 0
    avg_loss = abs(losses["pnl"].mean()) if loss_count else 0

    expectancy = (win_rate * avg_win) - (loss_rate * avg_loss)

    gross_profit = wins["pnl"].sum()
    gross_loss = abs(losses["pnl"].sum())
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    net_pnl = trades["pnl"].sum()
    max_win = trades["pnl"].max()
    max_loss = trades["pnl"].min()

    trades["equity_curve"] = trades["pnl"].cumsum()
    trades["running_max"] = trades["equity_curve"].cummax()
    trades["drawdown"] = trades["equity_curve"] - trades["running_max"]
    max_drawdown = trades["drawdown"].min()

    print("\nMES Strategy Performance")
    print("------------------------")
    print(f"Total completed trades: {total_trades}")
    print(f"Wins: {win_count}")
    print(f"Losses: {loss_count}")
    print(f"Win rate: {win_rate:.2%}")
    print(f"Average win: ${avg_win:.2f}")
    print(f"Average loss: ${avg_loss:.2f}")
    print(f"Expectancy/trade: ${expectancy:.2f}")
    print(f"Profit factor: {profit_factor:.2f}")
    print(f"Net PnL: ${net_pnl:.2f}")
    print(f"Best trade: ${max_win:.2f}")
    print(f"Worst trade: ${max_loss:.2f}")
    print(f"Max drawdown: ${max_drawdown:.2f}")

    print("\nBy strategy")
    print("-----------")
    by_strategy = trades.groupby("strategy")["pnl"].agg(
        trades="count",
        net_pnl="sum",
        avg_pnl="mean",
        best="max",
        worst="min"
    )
    print(by_strategy)

    if "timestamp" in trades.columns:
        trades["timestamp"] = pd.to_datetime(trades["timestamp"], errors="coerce")
        trades["hour"] = trades["timestamp"].dt.hour

        print("\nBy hour")
        print("-------")
        by_hour = trades.groupby("hour")["pnl"].agg(
            trades="count",
            net_pnl="sum",
            avg_pnl="mean"
        )
        print(by_hour)

    trades.to_csv("performance_report.csv", index=False)
    print("\nSaved detailed report to performance_report.csv")


if __name__ == "__main__":
    analyze()
