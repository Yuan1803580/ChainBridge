"""Add local protocol V1 funding, fee, deployment, and coordinator audit state."""

from alembic import op
import sqlalchemy as sa

from app.database import Base
from app import workflow_models  # noqa: F401


revision = "0004_local_protocol_v1"
down_revision = "0003_agent_candidate_pool"
branch_labels = None
depends_on = None


WORKFLOW_COLUMNS = (
    sa.Column("funding_mode", sa.String(24), nullable=False, server_default="RESERVED"),
    sa.Column("funding_status", sa.String(32), nullable=False, server_default="NOT_REQUIRED"),
    sa.Column("funding_wallet", sa.String(42), nullable=True),
    sa.Column("onchain_workflow_id", sa.String(66), nullable=True),
    sa.Column("lock_tx_hash", sa.String(66), nullable=True),
    sa.Column("source_gas_deposit", sa.Numeric(30, 12), nullable=False, server_default="0"),
    sa.Column("source_gas_refunded", sa.Numeric(30, 12), nullable=False, server_default="0"),
    sa.Column("lock_confirmed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("failure_refunded_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("contract_sync_status", sa.String(32), nullable=False, server_default="NOT_REQUIRED"),
)

ATTEMPT_COLUMNS = (
    sa.Column("result_proof_status", sa.String(32), nullable=False, server_default="NOT_REQUIRED"),
    sa.Column("result_proof_tx_hash", sa.String(66), nullable=True),
    sa.Column("coordinator_review_hash", sa.String(66), nullable=True),
    sa.Column("settlement_status", sa.String(32), nullable=False, server_default="NONE"),
    sa.Column("settlement_message_id", sa.String(128), nullable=True),
    sa.Column("destination_tx_hash", sa.String(66), nullable=True),
    sa.Column("source_confirm_tx_hash", sa.String(66), nullable=True),
)

ROUTE_COLUMNS = (
    sa.Column("deployment_status", sa.String(32), nullable=False, server_default="PLACEHOLDER"),
    sa.Column("deployment_manifest_hash", sa.String(66), nullable=True),
)

PROFILE_COLUMNS = (
    sa.Column("credential_status", sa.String(24), nullable=False, server_default="UNCONFIGURED"),
    sa.Column("credential_ref", sa.String(255), nullable=True),
    sa.Column("credential_fingerprint", sa.String(128), nullable=True),
)

FAILURE_COLUMNS = (
    sa.Column("route_paused", sa.Boolean(), nullable=False, server_default=sa.false()),
)

NEW_TABLES = (
    "workflow_gas_deposits",
    "workflow_fee_ledger_entries",
    "coordinator_decisions",
    "route_deployments",
    "workflow_protocol_jobs",
)


def _add_missing_columns(table_name: str, columns: tuple[sa.Column, ...]) -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}
    for column in columns:
        if column.name not in existing:
            op.add_column(table_name, column)


def upgrade() -> None:
    _add_missing_columns("workflows", WORKFLOW_COLUMNS)
    _add_missing_columns("node_attempts", ATTEMPT_COLUMNS)
    _add_missing_columns("chain_routes", ROUTE_COLUMNS)
    _add_missing_columns("workflow_agent_profiles", PROFILE_COLUMNS)
    _add_missing_columns("workflow_failure_incidents", FAILURE_COLUMNS)
    bind = op.get_bind()
    for table_name in NEW_TABLES:
        Base.metadata.tables[table_name].create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table_name in reversed(NEW_TABLES):
        Base.metadata.tables[table_name].drop(bind, checkfirst=True)
    for table_name, columns in (
        ("workflow_failure_incidents", FAILURE_COLUMNS),
        ("workflow_agent_profiles", PROFILE_COLUMNS),
        ("chain_routes", ROUTE_COLUMNS),
        ("node_attempts", ATTEMPT_COLUMNS),
        ("workflows", WORKFLOW_COLUMNS),
    ):
        with op.batch_alter_table(table_name) as batch:
            for column in reversed(columns):
                batch.drop_column(column.name)
