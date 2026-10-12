from __future__ import annotations

import hashlib


_MASK_64 = (1 << 64) - 1
_ROUND_CONSTANTS = (
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
)
_ROTATIONS = (
    (0, 36, 3, 41, 18),
    (1, 44, 10, 45, 2),
    (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56),
    (27, 20, 39, 8, 14),
)


def opaque_id_to_bytes32(value: str) -> str:
    """Map an opaque platform ID to the bytes32 identifier used by contracts."""
    return "0x" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def quote_terms_hash(order_id: str, base_terms_hash: str, quote_hash: str) -> str:
    """Match MarketplaceEscrow.buildQuoteTermsHash(bytes32,bytes32,bytes32)."""
    encoded = b"".join(_bytes32(value) for value in (order_id, base_terms_hash, quote_hash))
    return "0x" + keccak256(encoded).hex()


def _bytes32(value: str) -> bytes:
    if not value.startswith("0x") or len(value) != 66:
        raise ValueError("Expected a 0x-prefixed bytes32 value")
    return bytes.fromhex(value[2:])


def keccak256(data: bytes) -> bytes:
    """Ethereum Keccak-256 (legacy 0x01 domain padding), implemented dependency-free."""
    rate = 136
    padded = bytearray(data)
    padded.append(0x01)
    padded.extend(b"\x00" * ((rate - len(padded) % rate) % rate))
    padded[-1] |= 0x80

    state = [0] * 25
    for offset in range(0, len(padded), rate):
        block = padded[offset : offset + rate]
        for lane in range(rate // 8):
            state[lane] ^= int.from_bytes(block[lane * 8 : lane * 8 + 8], "little")
        _keccak_f1600(state)
    return b"".join(lane.to_bytes(8, "little") for lane in state)[:32]


def _rotate_left(value: int, shift: int) -> int:
    if shift == 0:
        return value
    return ((value << shift) | (value >> (64 - shift))) & _MASK_64


def _keccak_f1600(state: list[int]) -> None:
    for round_constant in _ROUND_CONSTANTS:
        columns = [
            state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^ state[x + 20]
            for x in range(5)
        ]
        deltas = [columns[(x - 1) % 5] ^ _rotate_left(columns[(x + 1) % 5], 1) for x in range(5)]
        for y in range(5):
            for x in range(5):
                state[x + 5 * y] ^= deltas[x]

        rotated = [0] * 25
        for y in range(5):
            for x in range(5):
                rotated[y + 5 * ((2 * x + 3 * y) % 5)] = _rotate_left(
                    state[x + 5 * y], _ROTATIONS[x][y]
                )

        for y in range(5):
            row = rotated[5 * y : 5 * y + 5]
            for x in range(5):
                state[x + 5 * y] = row[x] ^ ((~row[(x + 1) % 5]) & row[(x + 2) % 5])
                state[x + 5 * y] &= _MASK_64
        state[0] ^= round_constant
