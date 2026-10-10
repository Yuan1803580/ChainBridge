"""external Agent onboarding challenge proofs

Revision ID: 0010_external_agent_onboarding
Revises: 0009_native_gas_precision
"""

from alembic import op
import sqlalchemy as sa


revision = "0010_external_agent_onboarding"
down_revision = "0009_native_gas_precision"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    table_name = "external_agent_onboarding_challenges"
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        op.create_table(
            table_name,
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("profile_id", sa.String(length=36), sa.ForeignKey("workflow_agent_profiles.id"), nullable=False),
            sa.Column("purpose", sa.String(length=32), nullable=False),
            sa.Column("nonce", sa.String(length=96), nullable=False),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("nonce", name="uq_external_agent_onboarding_nonce"),
        )

    existing_indexes = {
        index["name"] for index in sa.inspect(bind).get_indexes(table_name)
    }
    for index_name, columns in (
        ("ix_external_agent_onboarding_challenges_profile_id", ["profile_id"]),
        ("ix_external_agent_onboarding_challenges_purpose", ["purpose"]),
        ("ix_external_agent_onboarding_challenges_nonce", ["nonce"]),
        ("ix_external_agent_onboarding_challenges_expires_at", ["expires_at"]),
    ):
        if index_name not in existing_indexes:
            op.create_index(index_name, table_name, columns)


def downgrade() -> None:
    op.drop_index("ix_external_agent_onboarding_challenges_expires_at", table_name="external_agent_onboarding_challenges")
    op.drop_index("ix_external_agent_onboarding_challenges_nonce", table_name="external_agent_onboarding_challenges")
    op.drop_index("ix_external_agent_onboarding_challenges_purpose", table_name="external_agent_onboarding_challenges")
    op.drop_index("ix_external_agent_onboarding_challenges_profile_id", table_name="external_agent_onboarding_challenges")
    op.drop_table("external_agent_onboarding_challenges")
