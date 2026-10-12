from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from eth_account import Account


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Provision a persistent local EVM Agent wallet.")
    parser.add_argument("--path", required=True)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--chain-id", required=True, type=int)
    return parser.parse_args()


def validated_wallet(path: Path, slug: str, chain_id: int) -> dict[str, object]:
    record = json.loads(path.read_text(encoding="utf-8-sig"))
    if record.get("schemaVersion") != 1 or record.get("slug") != slug or record.get("chainId") != chain_id:
        raise ValueError("Existing Agent wallet metadata does not match the requested Agent and chain")
    private_key = str(record.get("privateKey", ""))
    account = Account.from_key(private_key)
    if account.address.lower() != str(record.get("address", "")).lower():
        raise ValueError("Existing Agent wallet private key does not match its stored address")
    return record


def create_wallet(path: Path, slug: str, chain_id: int) -> dict[str, object]:
    account = Account.create()
    private_key = account.key.hex()
    if not private_key.startswith("0x"):
        private_key = "0x" + private_key
    record: dict[str, object] = {
        "schemaVersion": 1,
        "slug": slug,
        "chainId": chain_id,
        "address": account.address,
        "privateKey": private_key,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    return record


def main() -> None:
    args = parse_args()
    path = Path(args.path).resolve()
    already_existed = path.exists()
    record = validated_wallet(path, args.slug, args.chain_id) if already_existed else create_wallet(
        path, args.slug, args.chain_id
    )
    print(json.dumps({
        "created": not already_existed,
        "slug": record["slug"],
        "chainId": record["chainId"],
        "address": record["address"],
        "path": str(path),
    }))


if __name__ == "__main__":
    main()
