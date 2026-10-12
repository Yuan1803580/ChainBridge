"""Add requester funding evidence and persistent local Faucet issuance records.

Revision ID: 0016_requester_funding_evidence
Revises: 0015_agent_admission_chain_status
"""

from alembic import op
import sqlalchemy as sa


revision = "0016_requester_funding_evidence"
down_revision = "0015_agent_admission_chain_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("workflows")}
    additions = (
        sa.Column("approve_tx_hash", sa.String(length=66), nullable=True),
        sa.Column("source_gas_tx_hash", sa.String(length=66), nullable=True),
        sa.Column("deployment_id", sa.String(length=128), nullable=True),
    )
    for column in additions:
        if column.name not in columns:
            op.add_column("workflows", column)

    existing_indexes = {index["name"] for index in sa.inspect(bind).get_indexes("workflows")}
    for index_name, column in (
        ("uq_workflows_approve_tx_hash", "approve_tx_hash"),
        ("uq_workflows_lock_tx_hash", "lock_tx_hash"),
        ("uq_workflows_source_gas_tx_hash", "source_gas_tx_hash"),
    ):
        if index_name not in existing_indexes:
            op.create_index(index_name, "workflows", [column], unique=True)

    if "local_faucet_issuances" not in sa.inspect(bind).get_table_names():
        op.create_table(
            "local_faucet_issuances",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("request_id", sa.String(length=128), nullable=False),
            sa.Column("deployment_id", sa.String(length=128), nullable=False),
            sa.Column("actor_id", sa.String(length=36), nullable=False),
            sa.Column("wallet_address", sa.String(length=42), nullable=False),
            sa.Column("token_amount", sa.Numeric(precision=24, scale=8), nullable=False),
            sa.Column("native_amount", sa.Numeric(precision=38, scale=18), nullable=False),
            sa.Column("token_tx_hash", sa.String(length=66), nullable=True),
            sa.Column("native_tx_hash", sa.String(length=66), nullable=True),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="PENDING"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["actor_id"], ["agents.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("request_id"),
            sa.UniqueConstraint("token_tx_hash"),
            sa.UniqueConstraint("native_tx_hash"),
            sa.UniqueConstraint("deployment_id", "wallet_address", name="uq_local_faucet_deployment_wallet"),
        )
        op.create_index("ix_local_faucet_issuances_request_id", "local_faucet_issuances", ["request_id"])
        op.create_index("ix_local_faucet_issuances_deployment_id", "local_faucet_issuances", ["deployment_id"])
        op.create_index("ix_local_faucet_issuances_actor_id", "local_faucet_issuances", ["actor_id"])
        op.create_index("ix_local_faucet_issuances_wallet_address", "local_faucet_issuances", ["wallet_address"])
        op.create_index("ix_local_faucet_issuances_status", "local_faucet_issuances", ["status"])


def downgrade() -> None:
    op.drop_table("local_faucet_issuances")
    for index_name in (
        "uq_workflows_source_gas_tx_hash",
        "uq_workflows_lock_tx_hash",
        "uq_workflows_approve_tx_hash",
    ):
        op.drop_index(index_name, table_name="workflows")
    op.drop_column("workflows", "deployment_id")
    op.drop_column("workflows", "source_gas_tx_hash")
    op.drop_column("workflows", "approve_tx_hash")
