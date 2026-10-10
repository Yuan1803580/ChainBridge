"""store native Gas values with EVM precision

Revision ID: 0009_native_gas_precision
Revises: 0008_route_transport
"""

from alembic import op
import sqlalchemy as sa


revision = "0009_native_gas_precision"
down_revision = "0008_route_transport"
branch_labels = None
depends_on = None


GAS_COLUMNS = {
    "workflows": ("source_gas_deposit", "source_gas_refunded"),
    "workflow_gas_deposits": (
        "deposited_amount",
        "reserved_amount",
        "charged_amount",
        "refunded_amount",
    ),
}


def _alter_precision(precision: int, scale: int) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    available_tables = set(inspector.get_table_names())
    for table_name, column_names in GAS_COLUMNS.items():
        if table_name not in available_tables:
            continue
        columns = {column["name"]: column for column in sa.inspect(bind).get_columns(table_name)}
        for column_name in column_names:
            column = columns.get(column_name)
            if column is None:
                continue
            existing_type = column["type"]
            if (
                isinstance(existing_type, sa.Numeric)
                and existing_type.precision == precision
                and existing_type.scale == scale
            ):
                continue
            with op.batch_alter_table(table_name) as batch:
                batch.alter_column(
                    column_name,
                    existing_type=existing_type,
                    type_=sa.Numeric(precision, scale),
                    existing_nullable=column["nullable"],
                )


def upgrade() -> None:
    _alter_precision(38, 18)


def downgrade() -> None:
    _alter_precision(30, 12)
