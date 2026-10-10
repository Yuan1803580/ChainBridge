from __future__ import annotations

from app.chain_protocol import keccak256, quote_terms_hash


def test_keccak_and_quote_terms_hash_match_ethereum_abi_vectors():
    assert keccak256(b"").hex() == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    assert quote_terms_hash(
        "0x" + "aa" * 32,
        "0x" + "bb" * 32,
        "0x" + "cc" * 32,
    ) == "0x1ae35526dabfe21efe24a72e97d99a4be0482feeb298a4b123094fa635779e2d"
