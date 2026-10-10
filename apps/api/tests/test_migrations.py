from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import String, create_engine, inspect, text


REPO_ROOT = Path(__file__).resolve().parents[3]


def _upgrade(database_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(REPO_ROOT)
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")
    command.upgrade(config, "head")


def test_alembic_creates_a_fresh_database(tmp_path, monkeypatch):
    database_path = tmp_path / "fresh.db"
    _upgrade(database_path, monkeypatch)

    inspector = inspect(create_engine(f"sqlite:///{database_path.as_posix()}"))
    assert "dex_quotes" in inspector.get_table_names()
    assert "integration_request_nonces" in inspector.get_table_names()
    assert {
        "workflows",
        "workflow_nodes",
        "node_attempts",
        "budget_reservations",
        "chain_routes",
        "workflow_agent_profiles",
        "cross_chain_messages",
        "workflow_events",
        "workflow_ledger_entries",
        "agent_match_rounds",
        "agent_match_candidates",
        "workflow_failure_incidents",
        "workflow_review_approvals",
        "workflow_review_challenges",
    }.issubset(inspector.get_table_names())
    assert {column["name"] for column in inspector.get_columns("node_attempts")} >= {
        "node_id",
        "attempt_number",
        "chain_id",
        "route_id",
        "route_version",
        "match_candidate_id",
        "failure_reason_code",
        "failure_responsible_party",
        "settlement_asset",
        "payout_asset",
        "payout_token_address",
        "payout_decimals",
        "payout_rate",
        "payout_slippage_bps",
        "payout_quote_amount",
        "payout_min_amount",
        "payout_quote_expires_at",
        "payout_status",
        "payout_tx_hash",
        "review_status",
        "review_policy_hash",
        "review_sampled",
        "review_started_at",
        "challenge_expires_at",
    }
    assert {column["name"] for column in inspector.get_columns("workflow_nodes")} >= {
        "risk_level",
        "risk_assignment_mode",
        "risk_score",
        "risk_reasons",
        "review_policy_version",
        "review_policy_hash",
        "required_approvals",
        "challenge_period_seconds",
        "sampling_rate_bps",
        "review_budget",
    }
    assert {column["name"] for column in inspector.get_columns("chain_routes")} >= {
        "settlement_asset",
        "payout_asset",
        "payout_token_address",
        "payout_swap_address",
        "payout_decimals",
        "payout_rate",
        "payout_slippage_bps",
    }
    attempt_columns = {column["name"]: column for column in inspector.get_columns("node_attempts")}
    assert isinstance(attempt_columns["payout_quote_amount"]["type"], String)
    assert isinstance(attempt_columns["payout_min_amount"]["type"], String)
    assert {column["name"] for column in inspector.get_columns("workflow_agent_profiles")} >= {
        "price_floor",
        "success_rate_bps",
        "trust_score",
        "availability",
        "capacity_available",
        "gas_ready",
    }
    assert {column["name"] for column in inspector.get_columns("agent_invitations")} >= {
        "issuance_type",
        "bound_agent_key",
        "bound_did",
        "bound_runtime_id",
        "bound_home_wallet",
        "bound_public_key_sha256",
    }
    assert {column["name"] for column in inspector.get_columns("relayer_tasks")} >= {
        "lease_token",
        "first_broadcast_at",
        "last_checked_at",
        "transaction_nonce",
    }


def test_alembic_adds_hardening_columns_to_legacy_tables(tmp_path, monkeypatch):
    database_path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE dex_quotes (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE settlement_chain_events (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE relayer_tasks (id VARCHAR(36) PRIMARY KEY)"))

    _upgrade(database_path, monkeypatch)

    inspector = inspect(engine)
    assert {column["name"] for column in inspector.get_columns("dex_quotes")} >= {
        "max_sell_amount",
        "execution_hash",
        "consumed_at",
        "swap_transaction_hash",
    }
    assert {column["name"] for column in inspector.get_columns("settlement_chain_events")} >= {
        "token_address",
        "block_hash",
        "canonical_status",
        "orphaned_at",
    }
    assert {column["name"] for column in inspector.get_columns("relayer_tasks")} >= {
        "lease_token",
        "first_broadcast_at",
        "last_checked_at",
        "transaction_nonce",
    }
