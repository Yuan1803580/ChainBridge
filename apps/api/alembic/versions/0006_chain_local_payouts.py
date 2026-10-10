"""Add destination-chain payout asset conversion snapshots."""

from alembic import op
import sqlalchemy as sa


revision = "0006_chain_local_payouts"
down_revision = "0005_agent_home_wallet"
branch_labels = None
depends_on = None


ROUTE_COLUMNS = (
    sa.Column("settlement_asset", sa.String(32), nullable=False, server_default="USDC"),
    sa.Column("payout_asset", sa.String(32), nullable=False, server_default="USDC"),
    sa.Column("payout_token_address", sa.String(42), nullable=True),
    sa.Column("payout_swap_address", sa.String(42), nullable=True),
    sa.Column("payout_decimals", sa.Integer(), nullable=False, server_default="6"),
    sa.Column("payout_rate", sa.Numeric(30, 12), nullable=False, server_default="1"),
    sa.Column("payout_slippage_bps", sa.Integer(), nullable=False, server_default="100"),
)

ATTEMPT_COLUMNS = (
    sa.Column("settlement_asset", sa.String(32), nullable=False, server_default="USDC"),
    sa.Column("payout_asset", sa.String(32), nullable=False, server_default="USDC"),
    sa.Column("payout_token_address", sa.String(42), nullable=True),
    sa.Column("payout_decimals", sa.Integer(), nullable=False, server_default="6"),
    sa.Column("payout_rate", sa.Numeric(30, 12), nullable=False, server_default="1"),
    sa.Column("payout_slippage_bps", sa.Integer(), nullable=False, server_default="100"),
    sa.Column("payout_quote_amount", sa.String(80), nullable=True),
    sa.Column("payout_min_amount", sa.String(80), nullable=True),
    sa.Column("payout_quote_expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("payout_status", sa.String(24), nullable=False, server_default="NOT_REQUIRED"),
    sa.Column("payout_tx_hash", sa.String(66), nullable=True),
)


def _add_missing_columns(table_name: str, columns: tuple[sa.Column, ...]) -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}
    for column in columns:
        if column.name not in existing:
            op.add_column(table_name, column)


def upgrade() -> None:
    _add_missing_columns("chain_routes", ROUTE_COLUMNS)
    _add_missing_columns("node_attempts", ATTEMPT_COLUMNS)
    routes = sa.table(
        "chain_routes",
        sa.column("destination_chain_id", sa.Integer),
        sa.column("settlement_asset", sa.String),
        sa.column("payout_asset", sa.String),
        sa.column("payout_decimals", sa.Integer),
        sa.column("payout_rate", sa.Numeric),
        sa.column("payout_slippage_bps", sa.Integer),
    )
    for chain_id, symbol, decimals, rate in (
        (31338, "BTK", 18, "2"),
        (31339, "CTK", 8, "3"),
        (31340, "DTK", 6, "4"),
    ):
        op.execute(
            routes.update()
            .where(routes.c.destination_chain_id == chain_id)
            .values(
                settlement_asset="USDC",
                payout_asset=symbol,
                payout_decimals=decimals,
                payout_rate=rate,
                payout_slippage_bps=100,
            )
        )


def downgrade() -> None:
    for table_name, columns in (("node_attempts", ATTEMPT_COLUMNS), ("chain_routes", ROUTE_COLUMNS)):
        with op.batch_alter_table(table_name) as batch:
            for column in reversed(columns):
                batch.drop_column(column.name)
