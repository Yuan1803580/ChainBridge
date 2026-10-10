"""add explicit local or Chainlink CCIP route transport

Revision ID: 0008_route_transport
Revises: 0007_exact_payout_quote_strings
"""

from alembic import op
import sqlalchemy as sa


revision = "0008_route_transport"
down_revision = "0007_exact_payout_quote_strings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {column["name"] for column in inspector.get_columns("chain_routes")}
    columns = (
        sa.Column("transport", sa.String(length=32), nullable=False, server_default="LOCAL_MOCK"),
        sa.Column("source_chain_selector", sa.String(length=32), nullable=True),
        sa.Column("destination_chain_selector", sa.String(length=32), nullable=True),
        sa.Column("source_router_address", sa.String(length=42), nullable=True),
        sa.Column("destination_router_address", sa.String(length=42), nullable=True),
    )
    for column in columns:
        if column.name not in existing_columns:
            with op.batch_alter_table("chain_routes") as batch:
                batch.add_column(column)

    existing_indexes = {index["name"] for index in sa.inspect(bind).get_indexes("chain_routes")}
    if "ix_chain_routes_transport" not in existing_indexes:
        with op.batch_alter_table("chain_routes") as batch:
            batch.create_index("ix_chain_routes_transport", ["transport"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_indexes = {index["name"] for index in inspector.get_indexes("chain_routes")}
    if "ix_chain_routes_transport" in existing_indexes:
        with op.batch_alter_table("chain_routes") as batch:
            batch.drop_index("ix_chain_routes_transport")

    existing_columns = {column["name"] for column in sa.inspect(bind).get_columns("chain_routes")}
    for column_name in (
        "destination_router_address",
        "source_router_address",
        "destination_chain_selector",
        "source_chain_selector",
        "transport",
    ):
        if column_name in existing_columns:
            with op.batch_alter_table("chain_routes") as batch:
                batch.drop_column(column_name)
