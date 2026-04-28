from pathlib import Path
import sys
import pandas as pd
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]
DB_FILE = ROOT / "trades.db"

csv_path = Path(sys.argv[1])
symbol = sys.argv[2] if len(sys.argv) > 2 else "ES JUN26"

engine = create_engine(f"sqlite:///{DB_FILE}")

df = pd.read_csv(csv_path)
df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

# --- FORCE CLEAN TIMESTAMP PARSING ---

if "timestamp" in df.columns:
    # Strip whitespace + ensure string
    df["timestamp"] = df["timestamp"].astype(str).str.strip()

    # Parse using explicit format FIRST (fast + reliable)
    df["timestamp"] = pd.to_datetime(
        df["timestamp"],
        format="%Y-%m-%d %H:%M:%S",
        errors="coerce"
    )

    # Fallback if needed
    if df["timestamp"].isna().any():
        df["timestamp"] = pd.to_datetime(
            df["timestamp"],
            errors="coerce"
        )

elif "date" in df.columns and "time" in df.columns:
    combined = df["date"].astype(str).str.strip() + " " + df["time"].astype(str).str.strip()

    df["timestamp"] = pd.to_datetime(
        combined,
        format="%Y%m%d %H%M%S",
        errors="coerce"
    )

else:
    raise ValueError("Could not find timestamp/date/time columns.")

# 🔴 CRITICAL: enforce dtype again (guarantees .dt works)
df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")

# Drop bad rows
before = len(df)
df = df.dropna(subset=["timestamp"]).copy()
after = len(df)

print(f"Valid timestamps: {after}/{before}")

required = ["open", "high", "low", "close", "volume"]
missing = [c for c in required if c not in df.columns]

if missing:
    raise ValueError(f"Missing columns: {missing}. Found columns: {list(df.columns)}")

df = df.sort_values("timestamp").reset_index(drop=True)

df["symbol"] = symbol
df["price"] = df["close"]
df["session_date"] = df["timestamp"].dt.date

typical_price = (df["high"] + df["low"] + df["close"]) / 3
df["cum_pv"] = typical_price.mul(df["volume"]).groupby(df["session_date"]).cumsum()
df["cum_volume"] = df["volume"].groupby(df["session_date"]).cumsum()
df["vwap"] = df["cum_pv"] / df["cum_volume"]

df["avg_volume"] = df["volume"].rolling(20, min_periods=1).mean()

high_low = df["high"] - df["low"]
high_close = (df["high"] - df["close"].shift()).abs()
low_close = (df["low"] - df["close"].shift()).abs()
true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
df["atr"] = true_range.rolling(14, min_periods=1).mean()

delta = df["close"].diff()
gain = delta.clip(lower=0)
loss = -delta.clip(upper=0)

avg_gain = gain.rolling(14, min_periods=1).mean()
avg_loss = loss.rolling(14, min_periods=1).mean()

rs = avg_gain / avg_loss.replace(0, 1e-9)
df["rsi"] = 100 - (100 / (1 + rs))
df["rsi"] = df["rsi"].fillna(50)

def calc_orb(group):
    orb_window = group[
        (group["timestamp"].dt.time >= pd.Timestamp("08:30").time()) &
        (group["timestamp"].dt.time < pd.Timestamp("08:45").time())
    ]

    if orb_window.empty:
        group["orb_high"] = group["high"]
        group["orb_low"] = group["low"]
    else:
        group["orb_high"] = orb_window["high"].max()
        group["orb_low"] = orb_window["low"].min()

    return group

df = df.groupby("session_date", group_keys=False).apply(calc_orb)

sma20 = df["close"].rolling(20, min_periods=1).mean()
sma50 = df["close"].rolling(50, min_periods=1).mean()
atr20 = df["atr"].rolling(20, min_periods=1).mean()

df["trend_score"] = 0.0
df.loc[df["close"] > df["vwap"], "trend_score"] += 0.3
df.loc[sma20 > sma50, "trend_score"] += 0.3
df.loc[df["atr"] > atr20, "trend_score"] += 0.4

df["chop_score"] = 0.0
df.loc[(df["close"] - df["vwap"]).abs() < df["atr"] * 0.3, "chop_score"] += 0.3
df.loc[(sma20 - sma50).abs() < df["atr"] * 0.2, "chop_score"] += 0.3
df.loc[df["atr"] < atr20, "chop_score"] += 0.4

open_time = pd.to_datetime(df["timestamp"].dt.date.astype(str) + " 08:30:00")
df["minutes_after_open"] = ((df["timestamp"] - open_time).dt.total_seconds() / 60).astype(int)

out = df[
    [
        "timestamp",
        "symbol",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "avg_volume",
        "vwap",
        "atr",
        "rsi",
        "orb_high",
        "orb_low",
        "trend_score",
        "chop_score",
        "minutes_after_open",
    ]
].copy()

out["timestamp"] = out["timestamp"].astype(str)

# Load existing symbol + timestamp pairs
with engine.connect() as conn:
    existing = pd.read_sql(
        text("SELECT timestamp, symbol FROM market_bars WHERE symbol = :symbol"),
        conn,
        params={"symbol": symbol},
    )

if not existing.empty:
    existing["key"] = existing["symbol"].astype(str) + "|" + existing["timestamp"].astype(str)
    out["key"] = out["symbol"].astype(str) + "|" + out["timestamp"].astype(str)

    before = len(out)
    out = out[~out["key"].isin(existing["key"])].copy()
    skipped = before - len(out)

    out = out.drop(columns=["key"])
else:
    skipped = 0

if out.empty:
    print(f"No new rows imported. Skipped {skipped} existing rows.")
    print(f"DB: {DB_FILE}")
    sys.exit(0)

out.to_sql("market_bars", engine, if_exists="append", index=False)

print(f"Imported {len(out)} new rows into market_bars.")
print(f"Skipped {skipped} existing rows.")
print(f"DB: {DB_FILE}")

if (out["minutes_after_open"] < 0).all():
    print(
        "Warning: all imported rows are before 8:30 AM. "
        "This likely means your export is overnight data or not RTH-only."
    )
