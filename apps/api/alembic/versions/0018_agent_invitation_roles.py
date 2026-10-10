"""bind Agent roles to onboarding invitations

Revision ID: 0018_agent_invitation_roles
Revises: 0017_self_service_invitation_binding
"""
from alembic import op
import sqlalchemy as sa


revision = "0018_agent_invitation_roles"
down_revision = "0017_self_service_invitation_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {item["name"] for item in inspector.get_columns("agent_invitations")}
    if "roles" not in columns:
        op.add_column(
            "agent_invitations",
            sa.Column("roles", sa.JSON(), nullable=False, server_default='["provider"]'),
        )


def downgrade() -> None:
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("agent_invitations")}
    if "roles" in columns:
        with op.batch_alter_table("agent_invitations") as batch:
            batch.drop_column("roles")
