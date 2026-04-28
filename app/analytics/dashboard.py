"""Build `/dashboard/summary` payloads from completed-trade data."""

from __future__ import annotations

from typing import Any

import pandas as pd


def empty_dashboard_summary() -> dict[str, Any]:
    return {
        "total_trades": 0,
        "win_rate": 0,
        "net_pnl": 0,
        "expectancy": 0,
        "profit_factor": 0,
        "max_drawdown": 0,
        "equity_curve": [],
        "recent_trades": [],
    }


def build_dashboard_summary(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return empty_dashboard_summary()

    wins = df[df["pnl"] > 0]
    losses = df[df["pnl"] <= 0]

    total = len(df)
    win_rate = len(wins) / total if total else 0

    avg_win = wins["pnl"].mean() if len(wins) else 0
    avg_loss = abs(losses["pnl"].mean()) if len(losses) else 0
    loss_rate = 1 - win_rate

    expectancy = (win_rate * avg_win) - (loss_rate * avg_loss)

    gross_profit = wins["pnl"].sum()
    gross_loss = abs(losses["pnl"].sum())
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

    df = df.copy()
    df["equity"] = df["pnl"].cumsum()
    df["running_max"] = df["equity"].cummax()
    df["drawdown"] = df["equity"] - df["running_max"]

    return {
        "total_trades": total,
        "win_rate": round(win_rate * 100, 2),
        "net_pnl": round(df["pnl"].sum(), 2),
        "expectancy": round(expectancy, 2),
        "profit_factor": round(profit_factor, 2),
        "max_drawdown": round(df["drawdown"].min(), 2),
        "equity_curve": [
            {"trade": i + 1, "equity": round(row["equity"], 2)}
            for i, row in df.iterrows()
        ],
        "recent_trades": df.tail(20).to_dict(orient="records"),
    }
