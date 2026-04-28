"""Project filesystem layout (single source for repo root)."""

from __future__ import annotations

from pathlib import Path

# `app/` package directory
PACKAGE_ROOT: Path = Path(__file__).resolve().parent

# Repository root (parent of `app/`)
PROJECT_ROOT: Path = PACKAGE_ROOT.parent

DEFAULT_TRADES_DB_NAME: str = "trades.db"


def default_trades_db_path() -> Path:
    return PROJECT_ROOT / DEFAULT_TRADES_DB_NAME
