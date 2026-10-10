from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from eth_account import Account


def canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AgentSecrets:
    runtime_id: str
    agent_slug: str
    did: str
    home_wallet: str
    identity_key_file: Path
    wallet_key_file: Path
    credential_sha256: str
    home_chain_id: int
    route_key: str
    route_version: int
    allowed_contracts: frozenset[str]
    scopes: frozenset[str]
    max_gas_limit: int
    max_fee_per_gas: int
    max_priority_fee_per_gas: int
    max_total_fee_wei: int

    def identity_key(self):
        return serialization.load_pem_private_key(self.identity_key_file.read_bytes(), password=None)

    def wallet_account(self):
        value = self.wallet_key_file.read_text(encoding="utf-8").strip()
        account = Account.from_key(value)
        if account.address.lower() != self.home_wallet.lower():
            raise ValueError("Configured wallet key does not match homeWallet")
        return account


def load_agents() -> dict[str, AgentSecrets]:
    config_path = os.getenv("AGENT_SIGNER_CONFIG_FILE", "").strip()
    if not config_path:
        if os.getenv("APP_ENV", "development").lower() in {"production", "prod"}:
            raise ValueError("AGENT_SIGNER_CONFIG_FILE is required")
        return {}
    path = Path(config_path)
    if not path.exists():
        if os.getenv("SIDECAR_PROVISIONING_TOKEN", "") and os.getenv("SIDECAR_KEY_DIR", ""):
            return {}
        raise ValueError("AGENT_SIGNER_CONFIG_FILE does not exist")
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw.get("agents", []) if isinstance(raw, dict) else []
    agents: dict[str, AgentSecrets] = {}
    for item in items:
        runtime_id = str(item["runtimeId"])
        agents[runtime_id] = AgentSecrets(
            runtime_id=runtime_id,
            agent_slug=str(item.get("agentSlug", runtime_id)),
            did=str(item["did"]),
            home_wallet=str(item["homeWallet"]),
            identity_key_file=Path(item["identityKeyFile"]),
            wallet_key_file=Path(item["walletKeyFile"]),
            credential_sha256=str(item["credentialSha256"]).lower(),
            home_chain_id=int(item.get("homeChainId", 421614)),
            route_key=str(item["routeKey"]),
            route_version=int(item.get("routeVersion", 1)),
            allowed_contracts=frozenset(str(value).lower() for value in item.get("allowedContracts", [])),
            scopes=frozenset(str(value) for value in item.get("scopes", ["identity:sign", "result:sign", "wallet:sign", "status:write"])),
            max_gas_limit=int(item.get("maxGasLimit", 500000)),
            max_fee_per_gas=int(item.get("maxFeePerGas", "100000000000")),
            max_priority_fee_per_gas=int(item.get("maxPriorityFeePerGas", "5000000000")),
            max_total_fee_wei=int(item.get("maxTotalFeeWei", "50000000000000000")),
        )
    return agents


class ProvisioningConflict(ValueError):
    pass


_PROVISION_LOCK = threading.Lock()


@contextmanager
def _config_file_lock(config_path: Path) -> Iterator[None]:
    lock_path = config_path.with_suffix(config_path.suffix + ".lock")
    deadline = time.monotonic() + 10
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError("Timed out waiting for the Sidecar configuration lock")
            time.sleep(0.05)
    try:
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        yield
    finally:
        os.close(descriptor)
        lock_path.unlink(missing_ok=True)


def _write_private_file(path: Path, value: bytes) -> None:
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, value)
    finally:
        os.close(descriptor)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _atomic_json_write(path: Path, value: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    data = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    try:
        _write_private_file(temporary, data.encode("utf-8"))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _identity_public_key(private_key: ec.EllipticCurvePrivateKey) -> str:
    public = private_key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    return "es256k:" + public.hex()


def provision_agent_files(
    *,
    runtime_id: str,
    agent_slug: str,
    did: str,
    home_chain_id: int,
    route_key: str,
    route_version: int,
    allowed_contracts: list[str],
) -> dict[str, object]:
    config_value = os.getenv("AGENT_SIGNER_CONFIG_FILE", "").strip()
    key_dir_value = os.getenv("SIDECAR_KEY_DIR", "").strip()
    if not config_value or not key_dir_value:
        raise ValueError("AGENT_SIGNER_CONFIG_FILE and SIDECAR_KEY_DIR are required for provisioning")
    config_path = Path(config_value).expanduser().resolve()
    key_root = Path(key_dir_value).expanduser().resolve()
    key_root.mkdir(parents=True, exist_ok=True)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_dir = (key_root / runtime_id).resolve()
    try:
        runtime_dir.relative_to(key_root)
    except ValueError as exc:
        raise ProvisioningConflict("runtimeId resolves outside SIDECAR_KEY_DIR") from exc

    normalized_contracts = sorted({value.lower() for value in allowed_contracts})
    requested = {
        "runtimeId": runtime_id,
        "agentSlug": agent_slug,
        "did": did,
        "homeChainId": home_chain_id,
        "routeKey": route_key,
        "routeVersion": route_version,
        "allowedContracts": normalized_contracts,
    }
    with _PROVISION_LOCK, _config_file_lock(config_path):
        if config_path.exists():
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or not isinstance(raw.get("agents", []), list):
                raise ValueError("AGENT_SIGNER_CONFIG_FILE must contain an agents array")
        else:
            raw = {"agents": []}
        agents = raw.setdefault("agents", [])
        existing = next((item for item in agents if item.get("runtimeId") == runtime_id), None)
        if existing is not None:
            actual = {
                "runtimeId": str(existing.get("runtimeId")),
                "agentSlug": str(existing.get("agentSlug", runtime_id)),
                "did": str(existing.get("did")),
                "homeChainId": int(existing.get("homeChainId", 421614)),
                "routeKey": str(existing.get("routeKey")),
                "routeVersion": int(existing.get("routeVersion", 1)),
                "allowedContracts": sorted({str(value).lower() for value in existing.get("allowedContracts", [])}),
            }
            if actual != requested:
                raise ProvisioningConflict("runtimeId is already provisioned with different identity or policy fields")
            identity_file = Path(str(existing["identityKeyFile"]))
            wallet_file = Path(str(existing["walletKeyFile"]))
            credential_file = Path(str(existing.get("credentialFile", runtime_dir / "signer.credential")))
            if not identity_file.is_file() or not wallet_file.is_file() or not credential_file.is_file():
                raise ProvisioningConflict("runtimeId has an incomplete local key configuration")
            identity_key = serialization.load_pem_private_key(identity_file.read_bytes(), password=None)
            wallet = Account.from_key(wallet_file.read_text(encoding="utf-8").strip())
            return {
                **requested,
                "identityPublicKey": _identity_public_key(identity_key),
                "homeWallet": wallet.address,
                "signerCredentialRef": "file:" + str(credential_file),
                "idempotent": True,
            }

        if runtime_dir.exists():
            raise ProvisioningConflict("runtimeId key directory exists without a matching Sidecar configuration")
        if any(str(item.get("did")) == did for item in agents):
            raise ProvisioningConflict("did is already provisioned for another runtimeId")
        if any(str(item.get("agentSlug", item.get("runtimeId"))) == agent_slug for item in agents):
            raise ProvisioningConflict("agentSlug is already provisioned for another runtimeId")
        max_gas_limit = int(os.getenv("SIDECAR_PROVISION_MAX_GAS_LIMIT", "500000"))
        max_fee_per_gas = int(os.getenv("SIDECAR_PROVISION_MAX_FEE_PER_GAS", "100000000000"))
        max_priority_fee_per_gas = int(os.getenv("SIDECAR_PROVISION_MAX_PRIORITY_FEE_PER_GAS", "5000000000"))
        max_total_fee_wei = int(os.getenv("SIDECAR_PROVISION_MAX_TOTAL_FEE_WEI", "50000000000000000"))
        if (
            max_gas_limit < 21000
            or max_priority_fee_per_gas < 0
            or max_fee_per_gas < max_priority_fee_per_gas
            or max_total_fee_wei <= 0
        ):
            raise ValueError("Sidecar provisioning fee policy is invalid")
        runtime_dir.mkdir(mode=0o700)
        identity_file = runtime_dir / "identity.pem"
        wallet_file = runtime_dir / "wallet.key"
        credential_file = runtime_dir / "signer.credential"
        identity_key = ec.generate_private_key(ec.SECP256K1())
        wallet = Account.create()
        credential = secrets.token_urlsafe(48)
        _write_private_file(identity_file, identity_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))
        _write_private_file(wallet_file, (wallet.key.hex() + "\n").encode("ascii"))
        _write_private_file(credential_file, (credential + "\n").encode("ascii"))
        entry = {
            **requested,
            "homeWallet": wallet.address,
            "identityKeyFile": str(identity_file),
            "walletKeyFile": str(wallet_file),
            "credentialFile": str(credential_file),
            "credentialSha256": sha256_hex(credential),
            "scopes": ["identity:sign", "result:sign", "wallet:sign", "status:write"],
            "maxGasLimit": max_gas_limit,
            "maxFeePerGas": str(max_fee_per_gas),
            "maxPriorityFeePerGas": str(max_priority_fee_per_gas),
            "maxTotalFeeWei": str(max_total_fee_wei),
        }
        agents.append(entry)
        _atomic_json_write(config_path, raw)
        return {
            **requested,
            "identityPublicKey": _identity_public_key(identity_key),
            "homeWallet": wallet.address,
            "signerCredentialRef": "file:" + str(credential_file),
            "idempotent": False,
        }


def verify_worker_signature(payload: object, signature: str | None) -> None:
    secret = os.getenv("WORKER_INTENT_HMAC_SECRET", "")
    if not secret:
        raise ValueError("WORKER_INTENT_HMAC_SECRET is not configured")
    expected = "sha256=" + hmac.new(secret.encode(), canonical_json(payload), hashlib.sha256).hexdigest()
    if not signature or not hmac.compare_digest(signature, expected):
        raise PermissionError("Worker intent signature is invalid")


def storage_cipher() -> Fernet:
    configured = os.getenv("SIDECAR_STORAGE_KEY", "").encode()
    if not configured and os.getenv("APP_ENV", "development").lower() == "test":
        configured = base64.urlsafe_b64encode(hashlib.sha256(b"agent-signer-test-storage").digest())
    if not configured:
        raise ValueError("SIDECAR_STORAGE_KEY is required")
    return Fernet(configured)
