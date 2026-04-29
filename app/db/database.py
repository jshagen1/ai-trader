from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.paths import default_trades_db_path

_db_file = default_trades_db_path().resolve()
DATABASE_URL = f"sqlite:///{_db_file.as_posix()}"


class Base(DeclarativeBase):
    pass


engine: Engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)
