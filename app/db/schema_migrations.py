"""Lightweight SQLite column adds for DBs created before ORM columns existed."""

from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


def ensure_sqlite_schema(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    insp = inspect(engine)
    if not insp.has_table("completed_trades"):
        return

    existing = {col["name"] for col in insp.get_columns("completed_trades")}
    alters: list[str] = []
    if "slippage_points" not in existing:
        alters.append("ALTER TABLE completed_trades ADD COLUMN slippage_points FLOAT")
    if "slippage_dollars" not in existing:
        alters.append("ALTER TABLE completed_trades ADD COLUMN slippage_dollars FLOAT")

    if not alters:
        return

    with engine.begin() as conn:
        for stmt in alters:
            conn.execute(text(stmt))
