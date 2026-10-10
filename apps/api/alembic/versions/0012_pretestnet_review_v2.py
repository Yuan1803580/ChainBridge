"""Add post-result randomness, signed reviews and threshold arbitration.

Revision ID: 0012_pretestnet_review_v2
Revises: 0011_risk_based_reviews
"""

from alembic import op
import sqlalchemy as sa

from app.database import Base
from app import models, workflow_models  # noqa: F401
from app.security import demo_evm_wallet
from app.seed import ORGANIZATIONS


revision = "0012_pretestnet_review_v2"
down_revision = "0011_risk_based_reviews"
branch_labels = None
depends_on = None


AGENT_COLUMNS = (
    sa.Column("organization_id", sa.String(128), nullable=True),
    sa.Column("review_wallet", sa.String(42), nullable=True),
    sa.Column("reviewer_active", sa.Boolean(), nullable=False, server_default=sa.false()),
)

ATTEMPT_COLUMNS = (
    sa.Column("randomness_mode", sa.String(24), nullable=False, server_default="NOT_REQUIRED"),
    sa.Column("randomness_status", sa.String(24), nullable=False, server_default="NOT_REQUIRED"),
    sa.Column("randomness_request_id", sa.String(128), nullable=True),
    sa.Column("randomness_commitment", sa.String(66), nullable=True),
    sa.Column("random_word", sa.String(78), nullable=True),
    sa.Column("randomness_proof_hash", sa.String(66), nullable=True),
    sa.Column("randomness_requested_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("randomness_fulfilled_at", sa.DateTime(timezone=True), nullable=True),
)

APPROVAL_COLUMNS = (
    sa.Column("reviewer_wallet", sa.String(42), nullable=True),
    sa.Column("reviewer_organization_id", sa.String(128), nullable=True),
    sa.Column("signature", sa.Text(), nullable=True),
    sa.Column("signature_scheme", sa.String(24), nullable=True),
    sa.Column("signed_payload_hash", sa.String(66), nullable=True),
    sa.Column("nonce", sa.String(66), nullable=True),
    sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
)

CHALLENGE_COLUMNS = (
    sa.Column("arbitration_threshold", sa.Integer(), nullable=False, server_default="2"),
    sa.Column("onchain_review_id", sa.String(66), nullable=True),
    sa.Column("challenge_open_tx_hash", sa.String(66), nullable=True),
)


def _add_missing_columns(table_name: str, columns: tuple[sa.Column, ...]) -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}
    for column in columns:
        if column.name not in existing:
            op.add_column(table_name, column)


def _create_index_if_missing(table_name: str, name: str, columns: list[str], *, unique: bool = False) -> None:
    existing = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table_name)}
    if name not in existing:
        op.create_index(name, table_name, columns, unique=unique)


def upgrade() -> None:
    _add_missing_columns("agents", AGENT_COLUMNS)
    _add_missing_columns("node_attempts", ATTEMPT_COLUMNS)
    _add_missing_columns("workflow_review_approvals", APPROVAL_COLUMNS)
    _add_missing_columns("workflow_review_challenges", CHALLENGE_COLUMNS)
    Base.metadata.tables["workflow_arbitration_votes"].create(op.get_bind(), checkfirst=True)

    _create_index_if_missing("agents", "ix_agents_organization_id", ["organization_id"])
    _create_index_if_missing("agents", "ix_agents_reviewer_active", ["reviewer_active"])
    _create_index_if_missing("agents", "uq_agents_review_wallet", ["review_wallet"], unique=True)
    _create_index_if_missing("node_attempts", "ix_node_attempts_randomness_mode", ["randomness_mode"])
    _create_index_if_missing("node_attempts", "ix_node_attempts_randomness_status", ["randomness_status"])
    _create_index_if_missing("node_attempts", "uq_node_attempts_randomness_request_id", ["randomness_request_id"], unique=True)
    _create_index_if_missing("workflow_review_approvals", "ix_review_approvals_reviewer_wallet", ["reviewer_wallet"])
    _create_index_if_missing("workflow_review_approvals", "ix_review_approvals_reviewer_org", ["reviewer_organization_id"])
    _create_index_if_missing("workflow_review_approvals", "uq_review_approval_wallet_nonce", ["reviewer_wallet", "nonce"], unique=True)

    agents = sa.table(
        "agents",
        sa.column("slug", sa.String()),
        sa.column("roles", sa.JSON()),
        sa.column("organization_id", sa.String()),
        sa.column("review_wallet", sa.String()),
        sa.column("reviewer_active", sa.Boolean()),
    )
    bind = op.get_bind()
    for slug, organization_id in ORGANIZATIONS.items():
        reviewer_active = slug in {"code-review", "platform-admin", "arbitrator-one", "arbitrator-two", "arbitrator-three"}
        bind.execute(
            agents.update().where(agents.c.slug == slug).values(
                organization_id=organization_id,
                review_wallet=demo_evm_wallet(slug),
                reviewer_active=reviewer_active,
            )
        )


def downgrade() -> None:
    Base.metadata.tables["workflow_arbitration_votes"].drop(op.get_bind(), checkfirst=True)
    for table, indexes in (
        ("workflow_review_approvals", ["uq_review_approval_wallet_nonce", "ix_review_approvals_reviewer_org", "ix_review_approvals_reviewer_wallet"]),
        ("node_attempts", ["uq_node_attempts_randomness_request_id", "ix_node_attempts_randomness_status", "ix_node_attempts_randomness_mode"]),
        ("agents", ["uq_agents_review_wallet", "ix_agents_reviewer_active", "ix_agents_organization_id"]),
    ):
        existing = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table)}
        for name in indexes:
            if name in existing:
                op.drop_index(name, table_name=table)
    with op.batch_alter_table("workflow_review_challenges") as batch:
        for column in reversed(CHALLENGE_COLUMNS):
            batch.drop_column(column.name)
    with op.batch_alter_table("workflow_review_approvals") as batch:
        for column in reversed(APPROVAL_COLUMNS):
            batch.drop_column(column.name)
    with op.batch_alter_table("node_attempts") as batch:
        for column in reversed(ATTEMPT_COLUMNS):
            batch.drop_column(column.name)
    with op.batch_alter_table("agents") as batch:
        for column in reversed(AGENT_COLUMNS):
            batch.drop_column(column.name)
