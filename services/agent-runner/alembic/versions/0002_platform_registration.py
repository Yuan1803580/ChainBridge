"""persist managed platform heartbeat registration

Revision ID: runner_0002
Revises: runner_0001
"""
from alembic import op
import sqlalchemy as sa

revision = "runner_0002"
down_revision = "runner_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("managed_runtimes", sa.Column("platform_registration", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("managed_runtimes", "platform_registration")
