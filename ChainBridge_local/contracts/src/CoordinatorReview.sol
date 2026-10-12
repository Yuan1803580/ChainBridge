// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

interface IResultProofGateway {
    function hasProof(bytes32 attemptId, bytes32 resultHash, bytes32 proofHash) external view returns (bool);
}

contract CoordinatorReview {
    enum Decision { None, Pass, Fail, Disputed }

    struct Review {
        bytes32 criteriaHash;
        bytes32 resultHash;
        bytes32 proofHash;
        bytes32 reasonCode;
        bytes32 reviewHash;
        Decision decision;
        uint64 recordedAt;
    }

    address public immutable coordinator;
    IResultProofGateway public immutable resultGateway;
    mapping(bytes32 attemptId => Review review) public reviews;

    event ReviewRecorded(bytes32 indexed attemptId, Decision decision, bytes32 indexed reasonCode, bytes32 reviewHash);

    error Unauthorized();
    error InvalidInput();
    error ResultProofMissing();

    constructor(address _coordinator, IResultProofGateway _resultGateway) {
        if (_coordinator == address(0) || address(_resultGateway) == address(0)) revert InvalidInput();
        coordinator = _coordinator;
        resultGateway = _resultGateway;
    }

    function recordReview(
        bytes32 attemptId,
        bytes32 criteriaHash,
        bytes32 resultHash,
        bytes32 proofHash,
        Decision decision,
        bytes32 reasonCode,
        bytes32 reviewHash
    ) external {
        if (msg.sender != coordinator) revert Unauthorized();
        if (attemptId == bytes32(0) || criteriaHash == bytes32(0) || resultHash == bytes32(0) || proofHash == bytes32(0) || decision == Decision.None || reasonCode == bytes32(0) || reviewHash == bytes32(0) || reviews[attemptId].decision != Decision.None) {
            revert InvalidInput();
        }
        if (!resultGateway.hasProof(attemptId, resultHash, proofHash)) revert ResultProofMissing();
        reviews[attemptId] = Review(criteriaHash, resultHash, proofHash, reasonCode, reviewHash, decision, uint64(block.timestamp));
        emit ReviewRecorded(attemptId, decision, reasonCode, reviewHash);
    }
}
