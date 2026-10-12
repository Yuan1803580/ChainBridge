// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import {EIP712} from "@openzeppelin/contracts/utils/cryptography/EIP712.sol";

contract ReviewManagerV2 is EIP712 {
    using ECDSA for bytes32;

    enum ReviewStatus {
        None,
        ApprovalsPending,
        ChallengeOpen,
        Disputed,
        Approved,
        Rejected
    }

    struct ReviewState {
        bytes32 workflowId;
        bytes32 nodeId;
        bytes32 resultHash;
        bytes32 reviewPolicyHash;
        bytes32 challengeId;
        address requester;
        uint32 requiredApprovals;
        uint32 approvalCount;
        uint32 arbitrationThreshold;
        uint64 challengePeriod;
        uint64 challengeDeadline;
        ReviewStatus status;
    }

    bytes32 private constant REVIEW_APPROVAL_TYPEHASH = keccak256(
        "ReviewApproval(bytes32 workflowId,bytes32 nodeId,bytes32 attemptId,bytes32 resultHash,bytes32 reviewPolicyHash,uint8 decision,bytes32 evidenceHash,bytes32 notesHash,bytes32 nonce,uint256 deadline)"
    );
    bytes32 private constant ARBITRATION_VOTE_TYPEHASH = keccak256(
        "ArbitrationVote(bytes32 workflowId,bytes32 nodeId,bytes32 attemptId,bytes32 challengeId,bytes32 resultHash,uint8 resolution,bytes32 reasonHash,bytes32 nonce,uint256 deadline)"
    );

    address public immutable controller;
    mapping(address signer => bool active) public reviewers;
    mapping(address signer => bool active) public arbitrators;
    mapping(bytes32 attemptId => ReviewState state) public reviews;
    mapping(bytes32 attemptId => mapping(address signer => bool submitted)) public approvalSubmitted;
    mapping(bytes32 attemptId => mapping(address signer => bool submitted)) public arbitrationSubmitted;
    mapping(bytes32 attemptId => mapping(uint8 resolution => uint32 count)) public arbitrationVotes;
    mapping(address signer => mapping(bytes32 nonce => bool used)) public usedNonces;

    event ReviewerConfigured(address indexed signer, bool active);
    event ArbitratorConfigured(address indexed signer, bool active);
    event ReviewCreated(bytes32 indexed attemptId, bytes32 indexed workflowId, bytes32 indexed nodeId);
    event ReviewApprovalRecorded(bytes32 indexed attemptId, address indexed reviewer, uint8 decision);
    event ChallengePeriodOpened(bytes32 indexed attemptId, uint64 deadline);
    event ReviewChallenged(bytes32 indexed attemptId, bytes32 indexed challengeId, bytes32 evidenceHash);
    event ArbitrationVoteRecorded(bytes32 indexed attemptId, bytes32 indexed challengeId, address indexed arbitrator, uint8 resolution);
    event ReviewFinalized(bytes32 indexed attemptId, ReviewStatus status);

    error Unauthorized();
    error InvalidInput();
    error InvalidStatus();
    error SignatureExpired();
    error SignatureReplay();
    error DuplicateSigner();

    constructor(address _controller) EIP712("AgentCrosschainReview", "2") {
        if (_controller == address(0)) revert InvalidInput();
        controller = _controller;
    }

    modifier onlyController() {
        if (msg.sender != controller) revert Unauthorized();
        _;
    }

    function configureReviewer(address signer, bool active) external onlyController {
        if (signer == address(0)) revert InvalidInput();
        reviewers[signer] = active;
        emit ReviewerConfigured(signer, active);
    }

    function configureArbitrator(address signer, bool active) external onlyController {
        if (signer == address(0)) revert InvalidInput();
        arbitrators[signer] = active;
        emit ArbitratorConfigured(signer, active);
    }

    function createReview(
        bytes32 attemptId,
        bytes32 workflowId,
        bytes32 nodeId,
        bytes32 resultHash,
        bytes32 reviewPolicyHash,
        address requester,
        uint32 requiredApprovals,
        uint64 challengePeriod,
        uint32 arbitrationThreshold
    ) external onlyController {
        if (
            attemptId == bytes32(0) || workflowId == bytes32(0) || nodeId == bytes32(0)
                || resultHash == bytes32(0) || reviewPolicyHash == bytes32(0) || requester == address(0)
                || requiredApprovals == 0 || arbitrationThreshold < 2
                || reviews[attemptId].status != ReviewStatus.None
        ) revert InvalidInput();
        reviews[attemptId] = ReviewState({
            workflowId: workflowId,
            nodeId: nodeId,
            resultHash: resultHash,
            reviewPolicyHash: reviewPolicyHash,
            challengeId: bytes32(0),
            requester: requester,
            requiredApprovals: requiredApprovals,
            approvalCount: 0,
            arbitrationThreshold: arbitrationThreshold,
            challengePeriod: challengePeriod,
            challengeDeadline: 0,
            status: ReviewStatus.ApprovalsPending
        });
        emit ReviewCreated(attemptId, workflowId, nodeId);
    }

    function submitApproval(
        bytes32 attemptId,
        uint8 decision,
        bytes32 evidenceHash,
        bytes32 notesHash,
        bytes32 nonce,
        uint256 deadline,
        bytes calldata signature
    ) external {
        ReviewState storage review = reviews[attemptId];
        if (review.status != ReviewStatus.ApprovalsPending || (decision != 1 && decision != 2)) revert InvalidStatus();
        address signer = _recoverApproval(review, attemptId, decision, evidenceHash, notesHash, nonce, deadline, signature);
        if (!reviewers[signer]) revert Unauthorized();
        _consumeSigner(approvalSubmitted[attemptId], signer, nonce, deadline);
        emit ReviewApprovalRecorded(attemptId, signer, decision);
        if (decision == 2) {
            review.status = ReviewStatus.Rejected;
            emit ReviewFinalized(attemptId, review.status);
            return;
        }
        review.approvalCount += 1;
        if (review.approvalCount >= review.requiredApprovals) {
            if (review.challengePeriod == 0) {
                review.status = ReviewStatus.Approved;
                emit ReviewFinalized(attemptId, review.status);
                return;
            }
            review.challengeDeadline = uint64(block.timestamp) + review.challengePeriod;
            review.status = ReviewStatus.ChallengeOpen;
            emit ChallengePeriodOpened(attemptId, review.challengeDeadline);
        }
    }

    function challenge(bytes32 attemptId, bytes32 challengeId, bytes32 evidenceHash) external {
        ReviewState storage review = reviews[attemptId];
        if (
            review.status != ReviewStatus.ChallengeOpen || block.timestamp >= review.challengeDeadline
                || challengeId == bytes32(0) || evidenceHash == bytes32(0)
        ) revert InvalidStatus();
        if (msg.sender != review.requester && msg.sender != controller) revert Unauthorized();
        review.status = ReviewStatus.Disputed;
        review.challengeId = challengeId;
        emit ReviewChallenged(attemptId, challengeId, evidenceHash);
    }

    function finalizeUnchallenged(bytes32 attemptId) external {
        ReviewState storage review = reviews[attemptId];
        if (review.status != ReviewStatus.ChallengeOpen || block.timestamp < review.challengeDeadline) revert InvalidStatus();
        review.status = ReviewStatus.Approved;
        emit ReviewFinalized(attemptId, review.status);
    }

    function submitArbitrationVote(
        bytes32 attemptId,
        bytes32 challengeId,
        uint8 resolution,
        bytes32 reasonHash,
        bytes32 nonce,
        uint256 deadline,
        bytes calldata signature
    ) external {
        ReviewState storage review = reviews[attemptId];
        if (
            review.status != ReviewStatus.Disputed || challengeId != review.challengeId
                || (resolution != 1 && resolution != 2)
        ) revert InvalidStatus();
        address signer = _recoverArbitration(
            review, attemptId, challengeId, resolution, reasonHash, nonce, deadline, signature
        );
        if (!arbitrators[signer]) revert Unauthorized();
        _consumeSigner(arbitrationSubmitted[attemptId], signer, nonce, deadline);
        uint32 count = ++arbitrationVotes[attemptId][resolution];
        emit ArbitrationVoteRecorded(attemptId, challengeId, signer, resolution);
        if (count >= review.arbitrationThreshold) {
            review.status = resolution == 1 ? ReviewStatus.Approved : ReviewStatus.Rejected;
            emit ReviewFinalized(attemptId, review.status);
        }
    }

    function isSettlementAllowed(bytes32 attemptId) external view returns (bool) {
        return reviews[attemptId].status == ReviewStatus.Approved;
    }

    function _consumeSigner(
        mapping(address signer => bool submitted) storage submitted,
        address signer,
        bytes32 nonce,
        uint256 deadline
    ) private {
        if (block.timestamp > deadline) revert SignatureExpired();
        if (submitted[signer]) revert DuplicateSigner();
        if (usedNonces[signer][nonce]) revert SignatureReplay();
        submitted[signer] = true;
        usedNonces[signer][nonce] = true;
    }

    function _recoverApproval(
        ReviewState storage review,
        bytes32 attemptId,
        uint8 decision,
        bytes32 evidenceHash,
        bytes32 notesHash,
        bytes32 nonce,
        uint256 deadline,
        bytes calldata signature
    ) private view returns (address) {
        bytes32 structHash = keccak256(abi.encode(
            REVIEW_APPROVAL_TYPEHASH,
            review.workflowId,
            review.nodeId,
            attemptId,
            review.resultHash,
            review.reviewPolicyHash,
            decision,
            evidenceHash,
            notesHash,
            nonce,
            deadline
        ));
        return _hashTypedDataV4(structHash).recover(signature);
    }

    function _recoverArbitration(
        ReviewState storage review,
        bytes32 attemptId,
        bytes32 challengeId,
        uint8 resolution,
        bytes32 reasonHash,
        bytes32 nonce,
        uint256 deadline,
        bytes calldata signature
    ) private view returns (address) {
        bytes32 structHash = keccak256(abi.encode(
            ARBITRATION_VOTE_TYPEHASH,
            review.workflowId,
            review.nodeId,
            attemptId,
            challengeId,
            review.resultHash,
            resolution,
            reasonHash,
            nonce,
            deadline
        ));
        return _hashTypedDataV4(structHash).recover(signature);
    }
}
