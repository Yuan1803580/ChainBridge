"""managed Agent provisioning and explicit CCIP token semantics

Revision ID: 0015_managed_agent_testnet
Revises: 0014_review_manager_runtime
"""
from alembic import op
import sqlalchemy as sa
from app.database import Base
from app import workflow_models  # noqa: F401

revision = "0015_managed_agent_testnet"
down_revision = "0014_review_manager_runtime"
branch_labels = None
depends_on = None

LEGACY_SQLITE_NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    chain_columns = {item["name"] for item in inspector.get_columns("chain_routes")}
    for column in (
        sa.Column("source_token_address", sa.String(42), nullable=True),
        sa.Column("destination_token_address", sa.String(42), nullable=True),
        sa.Column("token_decimals", sa.Integer(), nullable=False, server_default="6"),
        sa.Column("settlement_mode", sa.String(32), nullable=False, server_default="mock"),
    ):
        if column.name not in chain_columns:
            op.add_column("chain_routes", column)

    Base.metadata.tables["agent_invitations"].create(bind, checkfirst=True)
    inspector = sa.inspect(bind)
    profile_columns = {item["name"] for item in inspector.get_columns("workflow_agent_profiles")}
    missing_profile_columns = []
    if "provisioning_id" not in profile_columns:
        missing_profile_columns.append(sa.Column("provisioning_id", sa.String(128), nullable=True))
    if "onboarding_status" not in profile_columns:
        missing_profile_columns.append(sa.Column("onboarding_status", sa.String(32), nullable=False, server_default="PENDING_VERIFICATION"))
    if "invitation_id" not in profile_columns:
        missing_profile_columns.append(sa.Column(
            "invitation_id",
            sa.String(36),
            sa.ForeignKey(
                "agent_invitations.id",
                name="fk_workflow_agent_profiles_invitation_id_agent_invitations",
            ),
            nullable=True,
        ))
    if missing_profile_columns:
        batch_options = (
            {"naming_convention": LEGACY_SQLITE_NAMING_CONVENTION}
            if bind.dialect.name == "sqlite"
            else {}
        )
        with op.batch_alter_table("workflow_agent_profiles", **batch_options) as batch:
            for column in missing_profile_columns:
                batch.add_column(column)
    Base.metadata.tables["agent_provision_outbox"].create(bind, checkfirst=True)

def downgrade() -> None:
    op.drop_table("agent_provision_outbox")
    with op.batch_alter_table("workflow_agent_profiles") as batch:
        batch.drop_column("invitation_id")
        batch.drop_column("onboarding_status")
        batch.drop_column("provisioning_id")
    op.drop_table("agent_invitations")
    with op.batch_alter_table("chain_routes") as batch:
        batch.drop_column("settlement_mode")
        batch.drop_column("token_decimals")
        batch.drop_column("destination_token_address")
        batch.drop_column("source_token_address")
