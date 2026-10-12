"""Register deployed protocol contracts used by signed review payloads.

Revision ID: 0014_review_manager_runtime
Revises: 0013_settlement_recovery
"""

from alembic import op

from app.database import Base
from app import workflow_models  # noqa: F401


revision = "0014_review_manager_runtime"
down_revision = "0013_settlement_recovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.tables["workflow_protocol_runtime_config"].create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    Base.metadata.tables["workflow_protocol_runtime_config"].drop(op.get_bind(), checkfirst=True)
