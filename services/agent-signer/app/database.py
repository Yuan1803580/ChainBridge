from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def database_url() -> str:
    return os.getenv("AGENT_SIGNER_DATABASE_URL", "sqlite:///./data/agent-signer.db")


def build_engine(url: str | None = None):
    resolved = url or database_url()
    if resolved.startswith("sqlite:///"):
        Path(resolved.removeprefix("sqlite:///" )).resolve().parent.mkdir(parents=True, exist_ok=True)
    return create_engine(
        resolved,
        connect_args={"check_same_thread": False, "timeout": 30} if resolved.startswith("sqlite") else {},
        pool_pre_ping=True,
    )


def session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)

