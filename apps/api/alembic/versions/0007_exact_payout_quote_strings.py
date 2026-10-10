"""Store destination payout quotes as exact decimal strings."""

from alembic import op
import sqlalchemy as sa


revision = "0007_exact_payout_quote_strings"
down_revision = "0006_chain_local_payouts"
branch_labels = None
depends_on = None


def _column_type(table_name: str, column_name: str) -> sa.types.TypeEngine:
    columns = sa.inspect(op.get_bind()).get_columns(table_name)
    return next(column["type"] for column in columns if column["name"] == column_name)


def upgrade() -> None:
    for column_name in ("payout_quote_amount", "payout_min_amount"):
        existing_type = _column_type("node_attempts", column_name)
        if isinstance(existing_type, sa.String):
            continue
        with op.batch_alter_table("node_attempts") as batch:
            batch.alter_column(
                column_name,
                existing_type=existing_type,
                type_=sa.String(80),
                existing_nullable=True,
            )


def downgrade() -> None:
    for column_name in ("payout_quote_amount", "payout_min_amount"):
        existing_type = _column_type("node_attempts", column_name)
        if isinstance(existing_type, sa.Numeric):
            continue
        with op.batch_alter_table("node_attempts") as batch:
            batch.alter_column(
                column_name,
                existing_type=existing_type,
                type_=sa.Numeric(36, 18),
                existing_nullable=True,
            )
