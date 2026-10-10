from __future__ import annotations

import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: get-es256k-public-key.py <private-key.pem>")
    private_key = serialization.load_pem_private_key(Path(sys.argv[1]).read_bytes(), password=None)
    if not isinstance(private_key, ec.EllipticCurvePrivateKey) or not isinstance(private_key.curve, ec.SECP256K1):
        raise ValueError("administrator identity key must use secp256k1")
    encoded = private_key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    print(encoded.hex())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
