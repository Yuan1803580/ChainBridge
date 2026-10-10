from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CanonicalResult(Base):
    __tablename__ = "canonical_results"
    __table_args__ = (UniqueConstraint("runtime_id", "attempt_id", name="uq_signer_result_attempt"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    runtime_id: Mapped[str] = mapped_column(String(128), index=True)
    workflow_id: Mapped[str] = mapped_column(String(128), index=True)
    attempt_id: Mapped[str] = mapped_column(String(128), index=True)
    result_message_id: Mapped[str] = mapped_column(String(66))
    result_hash: Mapped[str] = mapped_column(String(66))
    proof_hash: Mapped[str] = mapped_column(String(66))
    route_key: Mapped[str] = mapped_column(String(80))
    route_version: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)
    signature: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WalletNonce(Base):
    __tablename__ = "wallet_nonces"
    __table_args__ = (UniqueConstraint("chain_id", "wallet", name="uq_signer_wallet_chain"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chain_id: Mapped[int] = mapped_column(Integer)
    wallet: Mapped[str] = mapped_column(String(42))
    next_nonce: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class NonceReservation(Base):
    __tablename__ = "nonce_reservations"
    __table_args__ = (UniqueConstraint("chain_id", "wallet", "nonce", name="uq_signer_reserved_nonce"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chain_id: Mapped[int] = mapped_column(Integer, index=True)
    wallet: Mapped[str] = mapped_column(String(42), index=True)
    nonce: Mapped[int] = mapped_column(Integer)
    original_request_id: Mapped[str] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SignedTransaction(Base):
    __tablename__ = "signed_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    runtime_id: Mapped[str] = mapped_column(String(128), index=True)
    reservation_id: Mapped[int] = mapped_column(ForeignKey("nonce_reservations.id"), index=True)
    purpose: Mapped[str] = mapped_column(String(32))
    replaces_request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    intent: Mapped[dict] = mapped_column(JSON)
    raw_transaction_encrypted: Mapped[str] = mapped_column(Text)
    transaction_hash: Mapped[str] = mapped_column(String(66), index=True)
    status: Mapped[str] = mapped_column(String(32), default="SIGNED", index=True)
    broadcast_tx_hash: Mapped[str | None] = mapped_column(String(66), nullable=True)
    status_evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

