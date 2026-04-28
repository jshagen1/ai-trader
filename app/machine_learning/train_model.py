from __future__ import annotations

from pathlib import Path
from typing import Literal

import joblib
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report
from sklearn.model_selection import train_test_split
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from app.machine_learning.features import build_features
from app.paths import default_trades_db_path

DB = default_trades_db_path()
MODEL_PATH = Path(__file__).resolve().parent / "model.pkl"

LOOKAHEAD_BARS = 20
RISK_ATR_MULTIPLIER = 1.25
REWARD_MULTIPLIER = 2.0

engine: Engine = create_engine(f"sqlite:///{DB}")
bars = pd.read_sql("SELECT * FROM market_bars ORDER BY timestamp ASC", engine)

bars = bars.dropna(subset=[
    "open", "high", "low", "close", "volume",
    "vwap", "atr", "rsi", "orb_high", "orb_low",
    "trend_score", "chop_score"
]).reset_index(drop=True)


def did_hit_target_before_stop(
    bars: pd.DataFrame,
    index: int,
    direction: Literal["LONG", "SHORT"],
) -> int | None:
    row = bars.iloc[index]

    entry = row["close"]
    atr = row["atr"]
    risk = atr * RISK_ATR_MULTIPLIER

    if risk <= 0:
        return None

    future = bars.iloc[index + 1:index + 1 + LOOKAHEAD_BARS]

    if len(future) < LOOKAHEAD_BARS:
        return None

    if direction == "LONG":
        stop = entry - risk
        target = entry + risk * REWARD_MULTIPLIER

        for _, future_row in future.iterrows():
            if future_row["low"] <= stop:
                return 0
            if future_row["high"] >= target:
                return 1

    if direction == "SHORT":
        stop = entry + risk
        target = entry - risk * REWARD_MULTIPLIER

        for _, future_row in future.iterrows():
            if future_row["high"] >= stop:
                return 0
            if future_row["low"] <= target:
                return 1

    return None


rows = []

for i in range(len(bars) - LOOKAHEAD_BARS):
    row = bars.iloc[i]

    if row["close"] > row["orb_high"] and row["close"] > row["vwap"]:
        label = did_hit_target_before_stop(bars, i, "LONG")

        if label is not None:
            features = build_features(row)
            features["label"] = label
            rows.append(features)

    if row["close"] < row["orb_low"] and row["close"] < row["vwap"]:
        label = did_hit_target_before_stop(bars, i, "SHORT")

        if label is not None:
            features = build_features(row)
            features["label"] = label
            rows.append(features)

dataset = pd.DataFrame(rows)

if dataset.empty:
    raise ValueError("No labeled training samples found.")

X = dataset.drop(columns=["label"])
y = dataset["label"]

print("Training samples:", len(dataset))
print("Win labels:", int(y.sum()))
print("Loss labels:", int((y == 0).sum()))
print("Label win rate:", round(float(y.mean()), 4))

X_train, X_test, y_train, y_test = train_test_split(
    X,
    y,
    test_size=0.25,
    shuffle=False,
)

model = RandomForestClassifier(
    n_estimators=300,
    max_depth=6,
    random_state=42,
    class_weight="balanced",
)

model.fit(X_train, y_train)

preds = model.predict(X_test)

print(classification_report(y_test, preds, zero_division=0))

joblib.dump(model, MODEL_PATH)

print(f"Saved model to {MODEL_PATH}")
