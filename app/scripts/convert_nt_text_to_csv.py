from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd


def main() -> None:
    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])

    # Try ;-delimited first, no assumptions
    df = pd.read_csv(input_path, sep=";", header=None)

    print("Detected columns:", df.shape[1])
    print(df.head())

    # Common NinjaTrader no-header formats:
    # 6 cols: timestamp, open, high, low, close, volume
    # 7 cols: date, time, open, high, low, close, volume

    if df.shape[1] == 6:
        df.columns = ["timestamp", "open", "high", "low", "close", "volume"]
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")

    elif df.shape[1] == 7:
        df.columns = ["date", "time", "open", "high", "low", "close", "volume"]
        df["timestamp"] = pd.to_datetime(
            df["date"].astype(str).str.strip() + " " + df["time"].astype(str).str.strip(),
            errors="coerce",
        )

    else:
        raise ValueError(f"Unsupported column count: {df.shape[1]}")

    df = df.dropna(subset=["timestamp"])

    out = df[["timestamp", "open", "high", "low", "close", "volume"]].copy()
    out.to_csv(output_path, index=False)

    print(f"Converted {len(out)} rows")
    print(f"Saved to: {output_path}")


if __name__ == "__main__":
    main()
