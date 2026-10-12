"""Track destination custody and compensating settlement recovery.

Revision ID: 0013_settlement_recovery
Revises: 0012_pretestnet_review_v2
"""

from alembic import op

from app.database import Base
from app import workflow_models  # noqa: F401


revision = "0013_settlement_recovery"
down_revision = "0012_pretestnet_review_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.tables["workflow_settlement_recovery"].create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    Base.metadata.tables["workflow_settlement_recovery"].drop(op.get_bind(), checkfirst=True)
