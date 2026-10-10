from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ManagedRuntime(Base):
    __tablename__ = "managed_runtimes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provisioning_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    runtime_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    did: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    public_key_hex: Mapped[str] = mapped_column(Text)
    home_wallet: Mapped[str] = mapped_column(String(42), index=True)
    home_chain_id: Mapped[int] = mapped_column(Integer)
    route_key: Mapped[str] = mapped_column(String(80), index=True)
    route_version: Mapped[int] = mapped_column(Integer)
    capabilities: Mapped[list] = mapped_column(JSON)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[str] = mapped_column(String(32), default="1.0.0")
    executor_type: Mapped[str] = mapped_column(String(16))
    executor_config: Mapped[dict] = mapped_column(JSON)
    signer_url: Mapped[str] = mapped_column(String(500))
    signer_credential_ref: Mapped[str] = mapped_column(String(500))
    platform_registration: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ControlNonce(Base):
    __tablename__ = "runner_control_nonces"

    nonce: Mapped[str] = mapped_column(String(128), primary_key=True)
    used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
