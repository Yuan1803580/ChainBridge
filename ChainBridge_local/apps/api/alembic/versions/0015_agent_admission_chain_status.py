"""Separate Agent admission and home-chain readiness.

Revision ID: 0015_agent_admission_chain_status
Revises: 0014_review_manager_runtime
"""

from alembic import op
import sqlalchemy as sa


revision = "0015_agent_admission_chain_status"
down_revision = "0014_review_manager_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    table_name = "workflow_agent_profiles"
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns(table_name)}
    if "admission_status" not in columns:
        op.add_column(
            table_name,
            sa.Column("admission_status", sa.String(length=32), nullable=False, server_default="APPROVED"),
        )
    if "chain_status" not in columns:
        op.add_column(
            table_name,
            sa.Column("chain_status", sa.String(length=32), nullable=False, server_default="READY"),
        )

    existing_indexes = {index["name"] for index in sa.inspect(bind).get_indexes(table_name)}
    for index_name, column in (
        ("ix_workflow_agent_profiles_admission_status", "admission_status"),
        ("ix_workflow_agent_profiles_chain_status", "chain_status"),
    ):
        if index_name not in existing_indexes:
            op.create_index(index_name, table_name, [column])

    bind.execute(sa.text("""
        UPDATE workflow_agent_profiles
        SET admission_status = CASE
            WHEN credential_status = 'REFERENCE_ONLY' THEN 'PENDING_PLATFORM_REVIEW'
            WHEN credential_status = 'UNCONFIGURED' AND active = false THEN 'PENDING_OWNERSHIP'
            ELSE 'APPROVED'
        END
    """))
    bind.execute(sa.text("""
        UPDATE workflow_agent_profiles
        SET chain_status = CASE
            WHEN EXISTS (
                SELECT 1 FROM chain_routes
                WHERE chain_routes.source_chain_id = 31337
                  AND chain_routes.destination_chain_id = workflow_agent_profiles.chain_id
                  AND chain_routes.status = 'ACTIVE'
            ) THEN 'READY'
            ELSE 'PENDING_ROUTE'
        END
    """))


def downgrade() -> None:
    op.drop_index("ix_workflow_agent_profiles_chain_status", table_name="workflow_agent_profiles")
    op.drop_index("ix_workflow_agent_profiles_admission_status", table_name="workflow_agent_profiles")
    op.drop_column("workflow_agent_profiles", "chain_status")
    op.drop_column("workflow_agent_profiles", "admission_status")
