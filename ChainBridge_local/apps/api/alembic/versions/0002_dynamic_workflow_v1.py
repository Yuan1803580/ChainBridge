"""Create the local V1 dynamic workflow schema."""

from alembic import op

from app.database import Base
from app import workflow_models  # noqa: F401


revision = "0002_dynamic_workflow_v1"
down_revision = "0001_crosschain_hardening"
branch_labels = None
depends_on = None


TABLES = (
    "workflows",
    "workflow_nodes",
    "chain_routes",
    "workflow_agent_profiles",
    "node_attempts",
    "budget_reservations",
    "cross_chain_messages",
    "workflow_events",
    "workflow_ledger_entries",
)


def upgrade() -> None:
    bind = op.get_bind()
    for table_name in TABLES:
        Base.metadata.tables[table_name].create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table_name in reversed(TABLES):
        Base.metadata.tables[table_name].drop(bind, checkfirst=True)
