"""Create the hardened cross-chain persistence schema."""

from alembic import op
import sqlalchemy as sa

from app.database import Base
from app import models  # noqa: F401

revision = "0001_crosschain_hardening"
down_revision = None
branch_labels = None
depends_on = None


def _add_missing(table: str, columns: list[sa.Column]) -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {column["name"] for column in inspector.get_columns(table)}
    for column in columns:
        if column.name not in existing:
            op.add_column(table, column)


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind)
    _add_missing("dex_quotes", [
        sa.Column("max_sell_amount", sa.String(100), nullable=True),
        sa.Column("execution_hash", sa.String(66), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("swap_transaction_hash", sa.String(66), nullable=True),
    ])
    _add_missing("settlement_chain_events", [
        sa.Column("token_address", sa.String(42), nullable=True),
        sa.Column("block_hash", sa.String(66), nullable=True),
        sa.Column("canonical_status", sa.String(24), nullable=False, server_default="confirmed"),
        sa.Column("orphaned_at", sa.DateTime(timezone=True), nullable=True),
    ])
    _add_missing("relayer_tasks", [
        sa.Column("lease_token", sa.String(36), nullable=True),
        sa.Column("first_broadcast_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("transaction_nonce", sa.Integer(), nullable=True),
    ])


def downgrade() -> None:
    raise RuntimeError("The cross-chain hardening migration is intentionally irreversible")
