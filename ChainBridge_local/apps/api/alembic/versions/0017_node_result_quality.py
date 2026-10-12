"""Add auditable Coordinator Core node-result quality scores.

Revision ID: 0017_node_result_quality
Revises: 0016_requester_funding_evidence
"""

from alembic import op
import sqlalchemy as sa


revision = "0017_node_result_quality"
down_revision = "0016_requester_funding_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    table_name = "node_attempts"
    columns = {column["name"] for column in sa.inspect(bind).get_columns(table_name)}
    additions = (
        sa.Column("reported_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("matching_trust_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("matching_trust_source", sa.String(length=64), nullable=True),
        sa.Column("quality_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("quality_status", sa.String(length=24), nullable=False, server_default="NOT_CALCULATED"),
        sa.Column("quality_breakdown", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("quality_policy_version", sa.String(length=64), nullable=True),
        sa.Column("quality_evidence_hash", sa.String(length=66), nullable=True),
        sa.Column("quality_calculated_at", sa.DateTime(timezone=True), nullable=True),
    )
    for column in additions:
        if column.name not in columns:
            op.add_column(table_name, column)

    indexes = {index["name"] for index in sa.inspect(bind).get_indexes(table_name)}
    for index_name, column in (
        ("ix_node_attempts_quality_status", "quality_status"),
        ("ix_node_attempts_quality_evidence_hash", "quality_evidence_hash"),
    ):
        if index_name not in indexes:
            op.create_index(index_name, table_name, [column])


def downgrade() -> None:
    op.drop_index("ix_node_attempts_quality_evidence_hash", table_name="node_attempts")
    op.drop_index("ix_node_attempts_quality_status", table_name="node_attempts")
    for column in (
        "quality_calculated_at",
        "quality_evidence_hash",
        "quality_policy_version",
        "quality_breakdown",
        "quality_status",
        "quality_score",
        "matching_trust_score",
        "matching_trust_source",
        "reported_score",
    ):
        op.drop_column("node_attempts", column)
