from __future__ import annotations

import hashlib
import secrets
from typing import Any

import rfc8785


JCS_STANDARD = "RFC8785"


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize a JSON value using RFC 8785 JSON Canonicalization Scheme."""
    return rfc8785.dumps(value)


def canonical_sha256(value: Any, *, prefix: str = "0x") -> str:
    return prefix + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def verify_canonical_sha256(value: Any, expected: str, *, prefix: str = "0x") -> bool:
    return secrets.compare_digest(canonical_sha256(value, prefix=prefix), expected)
