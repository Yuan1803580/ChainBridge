"""Add ranked Agent candidate pools and failure incidents."""

from alembic import op
import sqlalchemy as sa

from app.database import Base
from app import workflow_models  # noqa: F401


revision = "0003_agent_candidate_pool"
down_revision = "0002_dynamic_workflow_v1"
branch_labels = None
depends_on = None


PROFILE_COLUMNS = (
    sa.Column("price_floor", sa.Numeric(24, 8), nullable=False, server_default="0"),
    sa.Column("currency", sa.String(32), nullable=False, server_default="USDC"),
    sa.Column("success_rate_bps", sa.Integer(), nullable=False, server_default="9000"),
    sa.Column("trust_score", sa.Integer(), nullable=False, server_default="80"),
    sa.Column("endpoint_latency_ms", sa.Integer(), nullable=False, server_default="500"),
    sa.Column("availability", sa.String(24), nullable=False, server_default="ONLINE"),
    sa.Column("capacity_available", sa.Boolean(), nullable=False, server_default=sa.true()),
    sa.Column("gas_ready", sa.Boolean(), nullable=False, server_default=sa.true()),
    sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("quote_expires_at", sa.DateTime(timezone=True), nullable=True),
)

ATTEMPT_COLUMNS = (
    sa.Column("match_candidate_id", sa.String(36), nullable=True),
    sa.Column("failure_reason_code", sa.String(64), nullable=True),
    sa.Column("failure_responsible_party", sa.String(24), nullable=True),
)

NEW_TABLES = (
    "agent_match_rounds",
    "agent_match_candidates",
    "workflow_failure_incidents",
)


def _add_missing_columns(table_name: str, columns: tuple[sa.Column, ...]) -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}
    for column in columns:
        if column.name not in existing:
            op.add_column(table_name, column)


def upgrade() -> None:
    _add_missing_columns("workflow_agent_profiles", PROFILE_COLUMNS)
    _add_missing_columns("node_attempts", ATTEMPT_COLUMNS)
    bind = op.get_bind()
    for table_name in NEW_TABLES:
        Base.metadata.tables[table_name].create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table_name in reversed(NEW_TABLES):
        Base.metadata.tables[table_name].drop(bind, checkfirst=True)
    with op.batch_alter_table("node_attempts") as batch:
        for column in reversed(ATTEMPT_COLUMNS):
            batch.drop_column(column.name)
    with op.batch_alter_table("workflow_agent_profiles") as batch:
        for column in reversed(PROFILE_COLUMNS):
            batch.drop_column(column.name)
