from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    app_env: str = "development"
    app_secret: str = "agent-market-local-development-only-secret"
    database_url: str = "sqlite:///./data/agent_market.db"
    cors_origins: tuple[str, ...] = ("http://localhost:3000", "http://127.0.0.1:3000")
    jwt_ttl_seconds: int = 3600
    challenge_ttl_seconds: int = 300
    settlement_mode: str = "mock"
    zero_ex_api_url: str = "https://api.0x.org"
    zero_ex_api_key: str = ""
    zero_ex_allowed_targets: tuple[str, ...] = ()
    zero_ex_allowed_allowance_targets: tuple[str, ...] = ()
    zero_ex_allowed_selectors: tuple[str, ...] = ()
    zero_ex_quote_ttl_seconds: int = 90
    source_chain_id: int = 11155111
    destination_chain_id: int = 421614
    source_chain_selector: str = ""
    destination_chain_selector: str = ""
    source_usdc_address: str = ""
    destination_usdc_address: str = ""
    destination_stc_address: str = ""
    source_rpc_url: str = ""
    destination_rpc_url: str = ""
    listener_hmac_secret: str = "listener-local-development-only-secret"
    listener_hmac_keys: tuple[tuple[str, str], ...] = ()
    listener_signature_window_seconds: int = 300
    relayer_lease_seconds: int = 300
    workflow_recovery_enabled: bool = True
    workflow_recovery_interval_seconds: float = 5.0
    workflow_recovery_max_workflows: int = 100
    workflow_recovery_max_actions: int = 4

    @classmethod
    def from_env(cls) -> "Settings":
        app_env = os.getenv("APP_ENV", "development")
        secret = os.getenv("APP_SECRET", cls.app_secret)
        if app_env == "production" and secret == cls.app_secret:
            raise RuntimeError("APP_SECRET must be set in production")
        listener_secret = os.getenv("LISTENER_HMAC_SECRET", "listener-local-development-only-secret")
        listener_keys = tuple(
            (worker_id.strip(), worker_secret.strip())
            for item in os.getenv("LISTENER_HMAC_KEYS", "").split(",")
            if item.strip()
            for worker_id, separator, worker_secret in (item.partition(":"),)
            if separator and worker_id.strip() and worker_secret.strip()
        )
        if app_env == "production" and not listener_keys:
            raise RuntimeError("LISTENER_HMAC_KEYS must configure at least one worker in production")
        origins = tuple(
            origin.strip()
            for origin in os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
            if origin.strip()
        )
        settlement_mode = os.getenv("SETTLEMENT_MODE", "mock")
        if settlement_mode not in {"mock", "external_ccip"}:
            raise RuntimeError("SETTLEMENT_MODE must be mock or external_ccip")
        return cls(
            app_env=app_env,
            app_secret=secret,
            database_url=os.getenv("DATABASE_URL", "sqlite:///./data/agent_market.db"),
            cors_origins=origins,
            jwt_ttl_seconds=int(os.getenv("JWT_TTL_SECONDS", "3600")),
            challenge_ttl_seconds=int(os.getenv("CHALLENGE_TTL_SECONDS", "300")),
            settlement_mode=settlement_mode,
            zero_ex_api_url=os.getenv("ZERO_EX_API_URL", "https://api.0x.org").rstrip("/"),
            zero_ex_api_key=os.getenv("ZERO_EX_API_KEY", ""),
            zero_ex_allowed_targets=tuple(
                address.strip().lower()
                for address in os.getenv("ZERO_EX_ALLOWED_TARGETS", "").split(",")
                if address.strip()
            ),
            zero_ex_allowed_allowance_targets=tuple(
                address.strip().lower()
                for address in os.getenv("ZERO_EX_ALLOWED_ALLOWANCE_TARGETS", "").split(",")
                if address.strip()
            ),
            zero_ex_allowed_selectors=tuple(
                selector.strip().lower()
                for selector in os.getenv("ZERO_EX_ALLOWED_SELECTORS", "").split(",")
                if selector.strip()
            ),
            zero_ex_quote_ttl_seconds=int(os.getenv("ZERO_EX_QUOTE_TTL_SECONDS", "90")),
            source_chain_id=int(os.getenv("SOURCE_CHAIN_ID", "11155111")),
            destination_chain_id=int(os.getenv("DESTINATION_CHAIN_ID", "421614")),
            source_chain_selector=os.getenv("SOURCE_CHAIN_SELECTOR", ""),
            destination_chain_selector=os.getenv("DESTINATION_CHAIN_SELECTOR", ""),
            source_usdc_address=os.getenv("SOURCE_SETTLEMENT_TOKEN", ""),
            destination_usdc_address=os.getenv("DESTINATION_SETTLEMENT_TOKEN", ""),
            destination_stc_address=os.getenv("DESTINATION_STC_TOKEN", ""),
            source_rpc_url=os.getenv("SOURCE_RPC_URL", ""),
            destination_rpc_url=os.getenv("DESTINATION_RPC_URL", ""),
            listener_hmac_secret=listener_secret,
            listener_hmac_keys=listener_keys,
            listener_signature_window_seconds=int(os.getenv("LISTENER_SIGNATURE_WINDOW_SECONDS", "300")),
            relayer_lease_seconds=int(os.getenv("RELAYER_LEASE_SECONDS", "300")),
            workflow_recovery_enabled=os.getenv("WORKFLOW_RECOVERY_ENABLED", "true").lower()
            in {"1", "true", "yes"},
            workflow_recovery_interval_seconds=max(
                0.5,
                float(os.getenv("WORKFLOW_RECOVERY_INTERVAL_SECONDS", "5")),
            ),
            workflow_recovery_max_workflows=max(
                1,
                int(os.getenv("WORKFLOW_RECOVERY_MAX_WORKFLOWS", "100")),
            ),
            workflow_recovery_max_actions=max(
                1,
                int(os.getenv("WORKFLOW_RECOVERY_MAX_ACTIONS", "4")),
            ),
        )
