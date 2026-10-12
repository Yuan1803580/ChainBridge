from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec


SECP256K1_ORDER = int(
    "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141", 16
)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def canonical_hash(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()


@dataclass(frozen=True)
class CoordinatorIdentity:
    agent_key: str
    did: str
    public_key_hex: str
    _private_key: ec.EllipticCurvePrivateKey

    def sign_message(self, message: str) -> str:
        signature = self._private_key.sign(
            message.encode("utf-8"), ec.ECDSA(hashes.SHA256())
        )
        return _b64url(signature)


def load_coordinator_identity() -> CoordinatorIdentity:
    agent_key = os.getenv("COORDINATOR_AGENT_KEY", "workflow-coordinator-agent").strip()
    private_key_hex = os.getenv("COORDINATOR_AGENT_PRIVATE_KEY", "").strip()
    environment = os.getenv("APP_ENV", "development").lower()
    if private_key_hex:
        try:
            private_value = int(private_key_hex.removeprefix("0x"), 16)
        except ValueError as exc:
            raise ValueError("COORDINATOR_AGENT_PRIVATE_KEY is not valid hex") from exc
    else:
        if environment in {"production", "prod"}:
            raise ValueError("COORDINATOR_AGENT_PRIVATE_KEY is required in production")
        development_seed = os.getenv("DEV_IDENTITY_SEED", "coordinator-agent-local-development")
        digest = hashlib.sha256(f"{development_seed}:{agent_key}".encode("utf-8")).digest()
        private_value = (int.from_bytes(digest, "big") % (SECP256K1_ORDER - 1)) + 1
    if private_value <= 0 or private_value >= SECP256K1_ORDER:
        raise ValueError("Coordinator identity private key is out of range")
    private_key = ec.derive_private_key(private_value, ec.SECP256K1())
    public_bytes = private_key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    did = os.getenv("COORDINATOR_AGENT_DID", f"did:local:{agent_key}").strip()
    return CoordinatorIdentity(
        agent_key=agent_key,
        did=did,
        public_key_hex="0x" + public_bytes.hex(),
        _private_key=private_key,
    )
