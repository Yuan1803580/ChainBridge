"""bind managed invitations to executor secret references

Revision ID: 0016_invitation_executor_secret_ref
Revises: 0015_managed_agent_testnet
"""
from alembic import op
import sqlalchemy as sa

revision = "0016_invitation_executor_secret_ref"
down_revision = "0015_managed_agent_testnet"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {item["name"] for item in inspector.get_columns("agent_invitations")}
    if "executor_type" not in columns:
        op.add_column("agent_invitations", sa.Column("executor_type", sa.String(24), nullable=False, server_default="llm"))
    if "executor_credential_ref" not in columns:
        op.add_column("agent_invitations", sa.Column("executor_credential_ref", sa.String(500), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("agent_invitations") as batch:
        batch.drop_column("executor_credential_ref")
        batch.drop_column("executor_type")
