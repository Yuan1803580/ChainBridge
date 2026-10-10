// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title IMarketplaceEscrow
/// @notice Extension boundary for replacing the V1 mock settlement adapter.
/// @dev UUIDs and signed terms are hashed to bytes32 before crossing the ABI boundary.
///      Implementations MUST add access control, reentrancy protection, token allowlists,
///      and decimal/amount validation. This interface does not move any assets itself.
interface IMarketplaceEscrow {
    enum SettlementStatus {
        None,
        Created,
        Locked,
        ReleasePending,
        Released,
        RefundPending,
        Refunded,
        Failed
    }

    struct CreateIntentParams {
        bytes32 settlementId;
        bytes32 orderId;
        bytes32 termsHash;
        address payer;
        address payee;
        address asset;
        uint256 amount;
        string payerAccount;
        string payeeAccount;
        string assetId;
        uint64 sourceChainSelector;
        uint64 destinationChainSelector;
    }

    struct SettlementIntent {
        bytes32 settlementId;
        bytes32 orderId;
        bytes32 termsHash;
        address payer;
        address payee;
        address asset;
        uint256 amount;
        string payerAccount;
        string payeeAccount;
        string assetId;
        uint64 sourceChainSelector;
        uint64 destinationChainSelector;
        SettlementStatus status;
        bytes32 ccipMessageId;
    }

    error SettlementAlreadyExists(bytes32 settlementId);
    error SettlementNotFound(bytes32 settlementId);
    error InvalidSettlementTransition(
        bytes32 settlementId,
        SettlementStatus current,
        SettlementStatus requested
    );
    error UnauthorizedSettlementActor(bytes32 settlementId, address actor);
    error InvalidSettlementAmount(bytes32 settlementId, uint256 amount);

    event SettlementIntentCreated(
        bytes32 indexed settlementId,
        bytes32 indexed orderId,
        address indexed payer,
        address payee,
        address asset,
        uint256 amount,
        bytes32 termsHash
    );
    event FundsLocked(
        bytes32 indexed settlementId,
        address indexed payer,
        address indexed asset,
        uint256 amount,
        bytes32 transactionReference
    );
    event ReleaseRequested(bytes32 indexed settlementId, address indexed actor);
    event FundsReleased(
        bytes32 indexed settlementId,
        address indexed payee,
        address indexed asset,
        uint256 amount,
        bytes32 transactionReference
    );
    event RefundRequested(bytes32 indexed settlementId, address indexed actor, bytes32 reasonHash);
    event FundsRefunded(
        bytes32 indexed settlementId,
        address indexed payer,
        address indexed asset,
        uint256 amount,
        bytes32 transactionReference
    );
    event SettlementFailed(bytes32 indexed settlementId, bytes32 indexed reasonHash);
    event CCIPMessageLinked(bytes32 indexed settlementId, bytes32 indexed messageId);

    function createIntent(CreateIntentParams calldata params) external returns (bytes32 settlementId);

    function lockFunds(bytes32 settlementId) external payable;

    function requestRelease(bytes32 settlementId) external;

    function releaseFunds(bytes32 settlementId) external returns (bytes32 transactionReference);

    function requestRefund(bytes32 settlementId, bytes32 reasonHash) external;

    function refund(bytes32 settlementId) external returns (bytes32 transactionReference);

    function markFailed(bytes32 settlementId, bytes32 reasonHash) external;

    function linkCCIPMessage(bytes32 settlementId, bytes32 messageId) external;

    function getSettlement(bytes32 settlementId) external view returns (SettlementIntent memory);
}

