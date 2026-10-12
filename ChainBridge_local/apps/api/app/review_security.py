from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any

from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils import keccak

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


def _bytes32(value: str | None) -> str:
    if value is not None and len(value) == 66 and value.startswith("0x"):
        return value
    compact = (value or "").replace("-", "")
    if re.fullmatch(r"[0-9a-fA-F]{1,64}", compact):
        return "0x" + compact.ljust(64, "0")
    return "0x" + keccak(text=value or "").hex()


def _deadline_timestamp(deadline: datetime) -> int:
    aware = deadline if deadline.tzinfo is not None else deadline.replace(tzinfo=timezone.utc)
    return int(aware.timestamp())


def _domain(chain_id: int, verifying_contract: str | None = None) -> dict[str, Any]:
    return {
        "name": "AgentCrosschainReview",
        "version": "2",
        "chainId": chain_id,
        "verifyingContract": verifying_contract or os.getenv("REVIEW_MANAGER_ADDRESS", ZERO_ADDRESS),
    }


def build_review_approval_typed_data(
    *,
    chain_id: int,
    workflow_id: str,
    node_id: str,
    attempt_id: str,
    result_hash: str,
    review_policy_hash: str,
    decision: str,
    evidence_hash: str,
    notes_hash: str,
    nonce: str,
    deadline: datetime,
    verifying_contract: str | None = None,
) -> dict[str, Any]:
    fields = [
        {"name": "workflowId", "type": "bytes32"},
        {"name": "nodeId", "type": "bytes32"},
        {"name": "attemptId", "type": "bytes32"},
        {"name": "resultHash", "type": "bytes32"},
        {"name": "reviewPolicyHash", "type": "bytes32"},
        {"name": "decision", "type": "uint8"},
        {"name": "evidenceHash", "type": "bytes32"},
        {"name": "notesHash", "type": "bytes32"},
        {"name": "nonce", "type": "bytes32"},
        {"name": "deadline", "type": "uint256"},
    ]
    return {
        "types": {"EIP712Domain": [
            {"name": "name", "type": "string"},
            {"name": "version", "type": "string"},
            {"name": "chainId", "type": "uint256"},
            {"name": "verifyingContract", "type": "address"},
        ], "ReviewApproval": fields},
        "primaryType": "ReviewApproval",
        "domain": _domain(chain_id, verifying_contract),
        "message": {
            "workflowId": _bytes32(workflow_id),
            "nodeId": _bytes32(node_id),
            "attemptId": _bytes32(attempt_id),
            "resultHash": _bytes32(result_hash),
            "reviewPolicyHash": _bytes32(review_policy_hash),
            "decision": 1 if decision == "PASS" else 2,
            "evidenceHash": _bytes32(evidence_hash),
            "notesHash": _bytes32(notes_hash),
            "nonce": _bytes32(nonce),
            "deadline": _deadline_timestamp(deadline),
        },
    }


def build_arbitration_vote_typed_data(
    *,
    chain_id: int,
    workflow_id: str,
    node_id: str,
    attempt_id: str,
    challenge_id: str,
    result_hash: str,
    resolution: str,
    reason_hash: str,
    nonce: str,
    deadline: datetime,
    verifying_contract: str | None = None,
) -> dict[str, Any]:
    return {
        "types": {"EIP712Domain": [
            {"name": "name", "type": "string"},
            {"name": "version", "type": "string"},
            {"name": "chainId", "type": "uint256"},
            {"name": "verifyingContract", "type": "address"},
        ], "ArbitrationVote": [
            {"name": "workflowId", "type": "bytes32"},
            {"name": "nodeId", "type": "bytes32"},
            {"name": "attemptId", "type": "bytes32"},
            {"name": "challengeId", "type": "bytes32"},
            {"name": "resultHash", "type": "bytes32"},
            {"name": "resolution", "type": "uint8"},
            {"name": "reasonHash", "type": "bytes32"},
            {"name": "nonce", "type": "bytes32"},
            {"name": "deadline", "type": "uint256"},
        ]},
        "primaryType": "ArbitrationVote",
        "domain": _domain(chain_id, verifying_contract),
        "message": {
            "workflowId": _bytes32(workflow_id),
            "nodeId": _bytes32(node_id),
            "attemptId": _bytes32(attempt_id),
            "challengeId": _bytes32(challenge_id),
            "resultHash": _bytes32(result_hash),
            "resolution": 1 if resolution == "UPHOLD_PASS" else 2,
            "reasonHash": _bytes32(reason_hash),
            "nonce": _bytes32(nonce),
            "deadline": _deadline_timestamp(deadline),
        },
    }


def recover_typed_signer(typed_data: dict[str, Any], signature: str) -> tuple[str, str]:
    signable = encode_typed_data(full_message=typed_data)
    signer = Account.recover_message(signable, signature=signature)
    digest = keccak(b"\x19" + signable.version + signable.header + signable.body)
    return signer, "0x" + digest.hex()
