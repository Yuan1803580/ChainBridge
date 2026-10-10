"""zero-RPC Agent signer state

Revision ID: signer_0001
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "signer_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("canonical_results",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("runtime_id", sa.String(128), nullable=False),
        sa.Column("workflow_id", sa.String(128), nullable=False),
        sa.Column("attempt_id", sa.String(128), nullable=False),
        sa.Column("result_message_id", sa.String(66), nullable=False),
        sa.Column("result_hash", sa.String(66), nullable=False),
        sa.Column("proof_hash", sa.String(66), nullable=False),
        sa.Column("route_key", sa.String(80), nullable=False),
        sa.Column("route_version", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("signature", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("runtime_id", "attempt_id", name="uq_signer_result_attempt"),
    )
    op.create_index("ix_signer_result_runtime", "canonical_results", ["runtime_id"])
    op.create_table("wallet_nonces",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("chain_id", sa.Integer(), nullable=False),
        sa.Column("wallet", sa.String(42), nullable=False),
        sa.Column("next_nonce", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("chain_id", "wallet", name="uq_signer_wallet_chain"),
    )
    op.create_table("nonce_reservations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("chain_id", sa.Integer(), nullable=False),
        sa.Column("wallet", sa.String(42), nullable=False),
        sa.Column("nonce", sa.Integer(), nullable=False),
        sa.Column("original_request_id", sa.String(128), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("chain_id", "wallet", "nonce", name="uq_signer_reserved_nonce"),
    )
    op.create_table("signed_transactions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("request_id", sa.String(128), nullable=False, unique=True),
        sa.Column("runtime_id", sa.String(128), nullable=False),
        sa.Column("reservation_id", sa.Integer(), sa.ForeignKey("nonce_reservations.id"), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("replaces_request_id", sa.String(128), nullable=True),
        sa.Column("intent", sa.JSON(), nullable=False),
        sa.Column("raw_transaction_encrypted", sa.Text(), nullable=False),
        sa.Column("transaction_hash", sa.String(66), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("broadcast_tx_hash", sa.String(66), nullable=True),
        sa.Column("status_evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_signer_transaction_request", "signed_transactions", ["request_id"], unique=True)


def downgrade() -> None:
    op.drop_table("signed_transactions")
    op.drop_table("nonce_reservations")
    op.drop_table("wallet_nonces")
    op.drop_table("canonical_results")

