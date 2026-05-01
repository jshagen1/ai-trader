"""Anti-martingale support: count consecutive losing trades for the current day."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.db.models_db import CompletedTrade


def get_loss_streak_today(db: Session, as_of_iso: str | None = None) -> int:
    """Count consecutive losses among today's completed trades, most-recent first.

    A win or break-even trade ends the streak. Used to scale down position size
    after 2 losses and halt entries after 3.
    """
    iso = as_of_iso or datetime.now().isoformat()
    date_prefix = iso[:10]

    rows = (
        db.query(CompletedTrade.pnl)
        .filter(CompletedTrade.timestamp.like(f"{date_prefix}%"))
        .order_by(CompletedTrade.id.desc())
        .all()
    )

    streak = 0
    for (pnl,) in rows:
        if pnl is None or pnl > 0:
            break
        streak += 1
    return streak
