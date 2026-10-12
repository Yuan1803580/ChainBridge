"""Add immutable risk-based node review policies and review records.

Revision ID: 0011_risk_based_reviews
Revises: 0010_external_agent_onboarding
"""

from alembic import op
import sqlalchemy as sa

from app.database import Base
from app import workflow_models  # noqa: F401


revision = "0011_risk_based_reviews"
down_revision = "0010_external_agent_onboarding"
branch_labels = None
depends_on = None


ZERO_HASH = "0x" + "0" * 64

NODE_COLUMNS = (
    sa.Column("risk_level", sa.String(16), nullable=False, server_default="LOW"),
    sa.Column("risk_assignment_mode", sa.String(16), nullable=False, server_default="AUTO"),
    sa.Column("risk_score", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("risk_reasons", sa.JSON(), nullable=False, server_default="[]"),
    sa.Column("review_policy_version", sa.String(64), nullable=False, server_default="RISK_REVIEW_V1"),
    sa.Column("review_policy_hash", sa.String(66), nullable=False, server_default=ZERO_HASH),
    sa.Column("required_approvals", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("challenge_period_seconds", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("sampling_rate_bps", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("review_budget", sa.Numeric(24, 8), nullable=False, server_default="0"),
)

ATTEMPT_COLUMNS = (
    sa.Column("review_status", sa.String(32), nullable=False, server_default="NOT_STARTED"),
    sa.Column("review_policy_hash", sa.String(66), nullable=True),
    sa.Column("review_sampled", sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column("review_started_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("challenge_expires_at", sa.DateTime(timezone=True), nullable=True),
)

NEW_TABLES = (
    "workflow_review_approvals",
    "workflow_review_challenges",
)


def _add_missing_columns(table_name: str, columns: tuple[sa.Column, ...]) -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}
    for column in columns:
        if column.name not in existing:
            op.add_column(table_name, column)


def upgrade() -> None:
    _add_missing_columns("workflow_nodes", NODE_COLUMNS)
    _add_missing_columns("node_attempts", ATTEMPT_COLUMNS)
    bind = op.get_bind()
    for table_name in NEW_TABLES:
        Base.metadata.tables[table_name].create(bind, checkfirst=True)

    inspector = sa.inspect(bind)
    existing_node_indexes = {item["name"] for item in inspector.get_indexes("workflow_nodes")}
    if "ix_workflow_nodes_risk_level" not in existing_node_indexes:
        op.create_index("ix_workflow_nodes_risk_level", "workflow_nodes", ["risk_level"])
    if "ix_workflow_nodes_risk_assignment_mode" not in existing_node_indexes:
        op.create_index("ix_workflow_nodes_risk_assignment_mode", "workflow_nodes", ["risk_assignment_mode"])
    existing_attempt_indexes = {item["name"] for item in sa.inspect(bind).get_indexes("node_attempts")}
    if "ix_node_attempts_review_status" not in existing_attempt_indexes:
        op.create_index("ix_node_attempts_review_status", "node_attempts", ["review_status"])


def downgrade() -> None:
    op.drop_index("ix_node_attempts_review_status", table_name="node_attempts")
    op.drop_index("ix_workflow_nodes_risk_assignment_mode", table_name="workflow_nodes")
    op.drop_index("ix_workflow_nodes_risk_level", table_name="workflow_nodes")
    bind = op.get_bind()
    for table_name in reversed(NEW_TABLES):
        Base.metadata.tables[table_name].drop(bind, checkfirst=True)
    with op.batch_alter_table("node_attempts") as batch:
        for column in reversed(ATTEMPT_COLUMNS):
            batch.drop_column(column.name)
    with op.batch_alter_table("workflow_nodes") as batch:
        for column in reversed(NODE_COLUMNS):
            batch.drop_column(column.name)
