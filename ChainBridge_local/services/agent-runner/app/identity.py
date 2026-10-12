from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
import rfc8785


SECP256K1_ORDER = int(
    "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141", 16
)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def canonical_json(value: object) -> bytes:
    return rfc8785.dumps(value)


def canonical_sha256(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()


def assert_canonical_sha256(value: object, expected: str) -> None:
    actual = canonical_sha256(value)
    if not secrets.compare_digest(actual, expected):
        raise ValueError("Agent output hash changed before submission")


@dataclass(frozen=True)
class RuntimeIdentity:
    did: str
    public_key_hex: str
    public_key_multibase: str
    _private_key: ec.EllipticCurvePrivateKey

    @classmethod
    def create(
        cls,
        slug: str,
        private_key_hex: str | None = None,
        *,
        did: str | None = None,
    ) -> "RuntimeIdentity":
        if private_key_hex:
            try:
                private_value = int(private_key_hex.removeprefix("0x"), 16)
            except ValueError as exc:
                raise ValueError(f"Invalid private key configured for {slug}") from exc
            if private_value <= 0 or private_value >= SECP256K1_ORDER:
                raise ValueError(f"Private key configured for {slug} is out of range")
            private_key = ec.derive_private_key(private_value, ec.SECP256K1())
        else:
            private_key = ec.generate_private_key(ec.SECP256K1())

        public_bytes = private_key.public_key().public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.CompressedPoint,
        )
        public_bytes_uncompressed = private_key.public_key().public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        )
        fingerprint = hashlib.sha256(public_bytes).hexdigest()[:24]
        return cls(
            did=did or f"did:agent:{slug}:{fingerprint}",
            public_key_hex="0x" + public_bytes_uncompressed.hex(),
            public_key_multibase=f"u{_b64url(public_bytes)}",
            _private_key=private_key,
        )

    def sign(self, payload: object) -> str:
        signature = self._private_key.sign(
            canonical_json(payload), ec.ECDSA(hashes.SHA256())
        )
        return _b64url(signature)

    def sign_message(self, message: str) -> str:
        signature = self._private_key.sign(
            message.encode("utf-8"), ec.ECDSA(hashes.SHA256())
        )
        return _b64url(signature)


def load_runtime_identities(slugs: list[str]) -> dict[str, RuntimeIdentity]:
    raw_keys = os.getenv("AGENT_RUNTIME_PRIVATE_KEYS_JSON", "")
    configured: dict[str, str] = {}
    if raw_keys:
        parsed = json.loads(raw_keys)
        if not isinstance(parsed, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in parsed.items()
        ):
            raise ValueError("AGENT_RUNTIME_PRIVATE_KEYS_JSON must be a string map")
        configured = parsed

    environment = os.getenv("APP_ENV", "development").lower()
    development_seed = os.getenv("DEV_IDENTITY_SEED")
    did_prefix = os.getenv("DEV_DID_PREFIX", "did:local").rstrip(":")
    identities: dict[str, RuntimeIdentity] = {}
    for slug in slugs:
        private_key_hex = configured.get(slug)
        if private_key_hex is None:
            if environment in {"production", "prod"}:
                raise ValueError(
                    f"Production identity key is not configured for {slug}"
                )
            if not development_seed:
                if environment == "test":
                    development_seed = "agent-market-test-identities"
                else:
                    raise ValueError(
                        "Set DEV_IDENTITY_SEED for stable development identities or "
                        "provide AGENT_RUNTIME_PRIVATE_KEYS_JSON"
                    )
            digest = hashlib.sha256(f"{development_seed}:{slug}".encode()).digest()
            private_value = (int.from_bytes(digest, "big") % (SECP256K1_ORDER - 1)) + 1
            private_key_hex = f"{private_value:064x}"
        identities[slug] = RuntimeIdentity.create(
            slug, private_key_hex, did=f"{did_prefix}:{slug}"
        )
    return identities
