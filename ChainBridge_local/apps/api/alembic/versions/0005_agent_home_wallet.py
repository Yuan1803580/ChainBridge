"""Snapshot Agent home wallets for dynamic settlement routing."""

from alembic import op
import sqlalchemy as sa


revision = "0005_agent_home_wallet"
down_revision = "0004_local_protocol_v1"
branch_labels = None
depends_on = None


TABLES = ("workflow_agent_profiles", "agent_match_candidates", "node_attempts")


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table_name in TABLES:
        if "home_wallet" not in {column["name"] for column in inspector.get_columns(table_name)}:
            op.add_column(table_name, sa.Column("home_wallet", sa.String(42), nullable=True))
    profiles = sa.table(
        "workflow_agent_profiles",
        sa.column("agent_key", sa.String),
        sa.column("home_wallet", sa.String),
    )
    for agent_key, home_wallet in (
        ("Agent-B1", "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"),
        ("Agent-C1", "0x90F79bf6EB2c4f870365E785982E1f101E93b906"),
        ("Agent-D1", "0x15d34AAf54267DB7D7c367839AAf71A00a2C6A65"),
    ):
        op.execute(
            profiles.update()
            .where(profiles.c.agent_key == agent_key)
            .values(home_wallet=home_wallet)
        )


def downgrade() -> None:
    for table_name in reversed(TABLES):
        with op.batch_alter_table(table_name) as batch:
            batch.drop_column("home_wallet")
