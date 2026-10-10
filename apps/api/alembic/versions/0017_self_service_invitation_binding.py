"""bind self-service invitations to one generated Agent identity

Revision ID: 0017_self_service_invitation_binding
Revises: 0016_invitation_executor_secret_ref
"""
from alembic import op
import sqlalchemy as sa

revision = "0017_self_service_invitation_binding"
down_revision = "0016_invitation_executor_secret_ref"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {item["name"] for item in inspector.get_columns("agent_invitations")}
    additions = (
        sa.Column("issuance_type", sa.String(24), nullable=False, server_default="ADMIN"),
        sa.Column("bound_agent_key", sa.String(80), nullable=True),
        sa.Column("bound_did", sa.String(255), nullable=True),
        sa.Column("bound_runtime_id", sa.String(128), nullable=True),
        sa.Column("bound_home_wallet", sa.String(42), nullable=True),
        sa.Column("bound_public_key_sha256", sa.String(64), nullable=True),
    )
    for column in additions:
        if column.name not in columns:
            op.add_column("agent_invitations", column)
    for column in ("issuance_type", "bound_agent_key", "bound_did", "bound_runtime_id"):
        index_name = f"ix_agent_invitations_{column}"
        indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("agent_invitations")}
        if index_name not in indexes:
            op.create_index(index_name, "agent_invitations", [column])


def downgrade() -> None:
    for column in ("bound_runtime_id", "bound_did", "bound_agent_key", "issuance_type"):
        op.drop_index(f"ix_agent_invitations_{column}", table_name="agent_invitations")
    with op.batch_alter_table("agent_invitations") as batch:
        batch.drop_column("bound_public_key_sha256")
        batch.drop_column("bound_home_wallet")
        batch.drop_column("bound_runtime_id")
        batch.drop_column("bound_did")
        batch.drop_column("bound_agent_key")
        batch.drop_column("issuance_type")
