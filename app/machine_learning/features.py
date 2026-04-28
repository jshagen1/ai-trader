from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def build_features(row: Mapping[str, Any]) -> dict[str, Any]:
    avg_volume = row.get("avg_volume", 0)

    if not avg_volume or avg_volume == 0:
        volume_ratio = 1.0
    else:
        volume_ratio = row["volume"] / avg_volume

    atr = row["atr"]
    return {
        "distance_from_vwap": row["close"] - row["vwap"],
        "distance_from_orb_high": row["close"] - row["orb_high"],
        "distance_from_orb_low": row["close"] - row["orb_low"],
        "atr": atr,
        "rsi": row["rsi"],
        "volume_ratio": volume_ratio,
        "trend_score": row["trend_score"],
        "chop_score": row["chop_score"],
        "vwap_distance_atr": (row["close"] - row["vwap"]) / atr if atr else 0,
        "body_size_atr": abs(row["close"] - row["open"]) / atr if atr else 0,
        "range_position": row.get("range_position", 0),
    }
