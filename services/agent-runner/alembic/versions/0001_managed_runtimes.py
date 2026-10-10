"""managed Runner runtime registry

Revision ID: runner_0001
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "runner_0001"
down_revision = None
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table("managed_runtimes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("provisioning_id", sa.String(128), nullable=False, unique=True),
        sa.Column("runtime_id", sa.String(128), nullable=False, unique=True),
        sa.Column("slug", sa.String(80), nullable=False, unique=True),
        sa.Column("did", sa.String(255), nullable=False, unique=True),
        sa.Column("public_key_hex", sa.Text(), nullable=False),
        sa.Column("home_wallet", sa.String(42), nullable=False),
        sa.Column("home_chain_id", sa.Integer(), nullable=False),
        sa.Column("route_key", sa.String(80), nullable=False),
        sa.Column("route_version", sa.Integer(), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("executor_type", sa.String(16), nullable=False),
        sa.Column("executor_config", sa.JSON(), nullable=False),
        sa.Column("signer_url", sa.String(500), nullable=False),
        sa.Column("signer_credential_ref", sa.String(500), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table("runner_control_nonces",
        sa.Column("nonce", sa.String(128), primary_key=True),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=False),
    )

def downgrade() -> None:
    op.drop_table("runner_control_nonces")
    op.drop_table("managed_runtimes")

