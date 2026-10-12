// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

library ExperimentProtocol {
    uint8 internal constant ASSIGNMENT = 1;
    uint8 internal constant RESULT = 2;
    uint8 internal constant SETTLEMENT = 3;
    uint8 internal constant SETTLEMENT_CONFIRMATION = 4;

    struct Envelope {
        uint8 messageType;
        bytes32 messageId;
        uint64 sourceChainId;
        uint64 targetChainId;
        address sourceSender;
        address targetReceiver;
        bytes32 workflowId;
        bytes32 nodeId;
        bytes32 attemptId;
        uint32 attemptNumber;
        bytes32 routeKey;
        uint32 routeVersion;
        uint256 nonce;
        bytes32 payloadHash;
    }

    struct SettlementRequest {
        bytes32 workflowId;
        bytes32 nodeId;
        bytes32 attemptId;
        uint32 attemptNumber;
        bytes32 routeKey;
        uint32 routeVersion;
        uint256 bridgeAmount;
        address payee;
        address payoutToken;
        uint256 minPayoutAmount;
        uint256 payoutDeadline;
    }

    function messageId(Envelope memory envelope) internal pure returns (bytes32) {
        envelope.messageId = bytes32(0);
        return keccak256(abi.encode(envelope));
    }

    function valid(Envelope memory envelope, bytes calldata payload) internal pure returns (bool) {
        bytes32 providedMessageId = envelope.messageId;
        return envelope.payloadHash == keccak256(payload) && providedMessageId == messageId(envelope);
    }
}
