// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

import {IRealCCIPSettlementSender} from "../interfaces/IRealCCIPSettlementSender.sol";
import {CCIPSettlementSender} from "./CCIPSettlementSender.sol";

contract MarketplaceEscrow {
    using SafeERC20 for IERC20;

    enum Status {
        None,
        Created,
        Locked,
        ReleasePending,
        Released,
        RefundPending,
        Refunded,
        Failed
    }

    struct Settlement {
        bytes32 orderId;
        bytes32 termsHash;
        address payer;
        address payee;
        IERC20 token;
        uint256 budgetAmount;
        uint256 paidAmount;
        Status status;
    }

    address public immutable platformSigner;
    CCIPSettlementSender public immutable ccipSender;
    uint64 public immutable destinationChainSelector;
    address public immutable destinationReceiver;
    IRealCCIPSettlementSender public realCcipSender;
    uint64 public realDestinationChainSelector;
    address public realDestinationReceiver;

    mapping(bytes32 => Settlement) public settlements;
    mapping(bytes32 => bytes32) public releaseMessageIds;

    event SettlementCreated(
        bytes32 indexed settlementId,
        bytes32 indexed orderId,
        bytes32 indexed termsHash,
        address payer,
        address payee,
        address token,
        uint256 budgetAmount
    );

    event FundsLocked(
        bytes32 indexed settlementId,
        address indexed payer,
        address token,
        uint256 amount
    );

    event FundsReleased(
        bytes32 indexed settlementId,
        address indexed payee,
        uint256 paidAmount,
        uint256 refundAmount
    );

    event FundsRefunded(
        bytes32 indexed settlementId,
        address indexed payer,
        uint256 refundAmount
    );

    event CrossChainFundsReleased(
        bytes32 indexed settlementId,
        address indexed payee,
        uint256 paidAmount,
        uint256 refundAmount,
        bytes32 indexed messageId
    );

    event RealCcipConfigUpdated(
        address indexed realCcipSender,
        uint64 destinationChainSelector,
        address indexed destinationReceiver
    );

    event RealCrossChainFundsReleased(
        bytes32 indexed settlementId,
        address indexed payee,
        uint256 paidAmount,
        uint256 refundAmount,
        bytes32 indexed messageId,
        uint256 nativeFee
    );

    event CrossChainReleaseConfirmed(
        bytes32 indexed settlementId,
        bytes32 indexed messageId,
        address indexed confirmer
    );

    error SettlementAlreadyExists(bytes32 settlementId);
    error SettlementNotFound(bytes32 settlementId);
    error InvalidStatus(bytes32 settlementId, Status current);
    error Unauthorized();
    error InvalidAmount();
    error InvalidConfig();
    error QuoteExpired(uint256 quoteDeadline, uint256 currentTimestamp);
    error InsufficientSettlementAmount(
        uint256 minSettlementAmount,
        uint256 providedAmount
    );
    error InvalidQuoteTerms(bytes32 expectedTermsHash, bytes32 providedTermsHash);
    error InsufficientNativeFee(uint256 requiredFee, uint256 providedFee);
    error NativeFeeRefundFailed();
    error InvalidMessageId(bytes32 expected, bytes32 provided);

    constructor(
        address _platformSigner,
        address _ccipSender,
        uint64 _destinationChainSelector,
        address _destinationReceiver
    ) {
        if (_ccipSender == address(0) || _destinationReceiver == address(0)) {
            revert InvalidConfig();
        }

        platformSigner = _platformSigner;
        ccipSender = CCIPSettlementSender(_ccipSender);
        destinationChainSelector = _destinationChainSelector;
        destinationReceiver = _destinationReceiver;
    }

    function setRealCcipConfig(
        address _realCcipSender,
        uint64 _realDestinationChainSelector,
        address _realDestinationReceiver
    ) external {
        if (msg.sender != platformSigner) {
            revert Unauthorized();
        }
        if (
            _realCcipSender == address(0) ||
            _realDestinationReceiver == address(0) ||
            _realDestinationChainSelector == 0
        ) {
            revert InvalidConfig();
        }

        realCcipSender = IRealCCIPSettlementSender(_realCcipSender);
        realDestinationChainSelector = _realDestinationChainSelector;
        realDestinationReceiver = _realDestinationReceiver;

        emit RealCcipConfigUpdated(
            _realCcipSender,
            _realDestinationChainSelector,
            _realDestinationReceiver
        );
    }

    function createAndLock(
        bytes32 settlementId,
        bytes32 orderId,
        bytes32 termsHash,
        address payee,
        address token,
        uint256 budgetAmount
    ) external {
        if (settlements[settlementId].status != Status.None) {
            revert SettlementAlreadyExists(settlementId);
        }
        if (payee == address(0) || token == address(0) || budgetAmount == 0) {
            revert InvalidAmount();
        }

        settlements[settlementId] = Settlement({
            orderId: orderId,
            termsHash: termsHash,
            payer: msg.sender,
            payee: payee,
            token: IERC20(token),
            budgetAmount: budgetAmount,
            paidAmount: 0,
            status: Status.Locked
        });

        IERC20(token).safeTransferFrom(msg.sender, address(this), budgetAmount);

        emit SettlementCreated(
            settlementId,
            orderId,
            termsHash,
            msg.sender,
            payee,
            token,
            budgetAmount
        );
        emit FundsLocked(settlementId, msg.sender, token, budgetAmount);
    }

    function createAndLockWithQuote(
        bytes32 settlementId,
        bytes32 orderId,
        bytes32 baseTermsHash,
        bytes32 quoteHash,
        bytes32 termsHash,
        address payee,
        address token,
        uint256 budgetAmount,
        uint256 minSettlementAmount,
        uint256 quoteDeadline
    ) external {
        if (settlements[settlementId].status != Status.None) {
            revert SettlementAlreadyExists(settlementId);
        }
        if (
            payee == address(0) ||
            token == address(0) ||
            budgetAmount == 0 ||
            minSettlementAmount == 0
        ) {
            revert InvalidAmount();
        }
        if (block.timestamp > quoteDeadline) {
            revert QuoteExpired(quoteDeadline, block.timestamp);
        }
        if (budgetAmount < minSettlementAmount) {
            revert InsufficientSettlementAmount(
                minSettlementAmount,
                budgetAmount
            );
        }

        bytes32 expectedTermsHash = buildQuoteTermsHash(
            orderId,
            baseTermsHash,
            quoteHash
        );
        if (termsHash != expectedTermsHash) {
            revert InvalidQuoteTerms(expectedTermsHash, termsHash);
        }

        settlements[settlementId] = Settlement({
            orderId: orderId,
            termsHash: termsHash,
            payer: msg.sender,
            payee: payee,
            token: IERC20(token),
            budgetAmount: budgetAmount,
            paidAmount: 0,
            status: Status.Locked
        });

        IERC20(token).safeTransferFrom(msg.sender, address(this), budgetAmount);

        emit SettlementCreated(
            settlementId,
            orderId,
            termsHash,
            msg.sender,
            payee,
            token,
            budgetAmount
        );
        emit FundsLocked(settlementId, msg.sender, token, budgetAmount);
    }

    function buildQuoteTermsHash(
        bytes32 orderId,
        bytes32 baseTermsHash,
        bytes32 quoteHash
    ) public pure returns (bytes32) {
        return keccak256(abi.encode(orderId, baseTermsHash, quoteHash));
    }

    function release(bytes32 settlementId, uint256 paidAmount) external {
        Settlement storage settlement = settlements[settlementId];

        if (settlement.status == Status.None) {
            revert SettlementNotFound(settlementId);
        }
        if (settlement.status != Status.Locked) {
            revert InvalidStatus(settlementId, settlement.status);
        }
        if (msg.sender != settlement.payer && msg.sender != platformSigner) {
            revert Unauthorized();
        }
        if (paidAmount > settlement.budgetAmount) {
            revert InvalidAmount();
        }

        settlement.status = Status.Released;
        settlement.paidAmount = paidAmount;

        uint256 refundAmount = settlement.budgetAmount - paidAmount;

        if (paidAmount > 0) {
            settlement.token.safeTransfer(settlement.payee, paidAmount);
        }
        if (refundAmount > 0) {
            settlement.token.safeTransfer(settlement.payer, refundAmount);
        }

        emit FundsReleased(
            settlementId,
            settlement.payee,
            paidAmount,
            refundAmount
        );
    }

    function releaseCrossChain(
        bytes32 settlementId,
        uint256 paidAmount
    ) external returns (bytes32 messageId) {
        Settlement storage settlement = settlements[settlementId];

        if (settlement.status == Status.None) {
            revert SettlementNotFound(settlementId);
        }
        if (settlement.status != Status.Locked) {
            revert InvalidStatus(settlementId, settlement.status);
        }
        if (msg.sender != settlement.payer && msg.sender != platformSigner) {
            revert Unauthorized();
        }
        if (paidAmount == 0 || paidAmount > settlement.budgetAmount) {
            revert InvalidAmount();
        }

        settlement.status = Status.ReleasePending;
        settlement.paidAmount = paidAmount;

        uint256 refundAmount = settlement.budgetAmount - paidAmount;

        if (refundAmount > 0) {
            settlement.token.safeTransfer(settlement.payer, refundAmount);
        }

        settlement.token.forceApprove(address(ccipSender), paidAmount);
        messageId = ccipSender.sendSettlementFromEscrow(
            destinationChainSelector,
            destinationReceiver,
            address(settlement.token),
            CCIPSettlementSender.SettlementPayload({
                settlementId: settlementId,
                orderId: settlement.orderId,
                payer: settlement.payer,
                payee: settlement.payee,
                amount: paidAmount
            })
        );
        releaseMessageIds[settlementId] = messageId;

        emit FundsReleased(
            settlementId,
            settlement.payee,
            paidAmount,
            refundAmount
        );
        emit CrossChainFundsReleased(
            settlementId,
            settlement.payee,
            paidAmount,
            refundAmount,
            messageId
        );
    }

    function quoteRealCrossChainFee(
        bytes32 settlementId,
        uint256 paidAmount
    ) external view returns (uint256 fee) {
        Settlement storage settlement = settlements[settlementId];

        if (settlement.status == Status.None) {
            revert SettlementNotFound(settlementId);
        }
        if (settlement.status != Status.Locked) {
            revert InvalidStatus(settlementId, settlement.status);
        }
        if (paidAmount == 0 || paidAmount > settlement.budgetAmount) {
            revert InvalidAmount();
        }
        if (
            address(realCcipSender) == address(0) ||
            realDestinationReceiver == address(0) ||
            realDestinationChainSelector == 0
        ) {
            revert InvalidConfig();
        }

        return
            realCcipSender.quoteFee(
                realDestinationChainSelector,
                realDestinationReceiver,
                address(settlement.token),
                paidAmount
            );
    }

    function releaseRealCrossChain(
        bytes32 settlementId,
        uint256 paidAmount
    ) external payable returns (bytes32 messageId) {
        Settlement storage settlement = settlements[settlementId];

        if (settlement.status == Status.None) {
            revert SettlementNotFound(settlementId);
        }
        if (settlement.status != Status.Locked) {
            revert InvalidStatus(settlementId, settlement.status);
        }
        if (msg.sender != settlement.payer && msg.sender != platformSigner) {
            revert Unauthorized();
        }
        if (paidAmount == 0 || paidAmount > settlement.budgetAmount) {
            revert InvalidAmount();
        }
        if (
            address(realCcipSender) == address(0) ||
            realDestinationReceiver == address(0) ||
            realDestinationChainSelector == 0
        ) {
            revert InvalidConfig();
        }

        uint256 nativeFee = realCcipSender.quoteFee(
            realDestinationChainSelector,
            realDestinationReceiver,
            address(settlement.token),
            paidAmount
        );
        if (msg.value < nativeFee) {
            revert InsufficientNativeFee(nativeFee, msg.value);
        }

        settlement.status = Status.ReleasePending;
        settlement.paidAmount = paidAmount;

        uint256 refundAmount = settlement.budgetAmount - paidAmount;

        if (refundAmount > 0) {
            settlement.token.safeTransfer(settlement.payer, refundAmount);
        }

        settlement.token.forceApprove(address(realCcipSender), paidAmount);
        messageId = realCcipSender.sendSettlementFromEscrowPayNative{
            value: nativeFee
        }(
            realDestinationChainSelector,
            realDestinationReceiver,
            address(settlement.token),
            IRealCCIPSettlementSender.SettlementPayload({
                settlementId: settlementId,
                orderId: settlement.orderId,
                payer: settlement.payer,
                payee: settlement.payee,
                amount: paidAmount
            })
        );
        releaseMessageIds[settlementId] = messageId;

        if (msg.value > nativeFee) {
            (bool refunded, ) = msg.sender.call{value: msg.value - nativeFee}(
                ""
            );
            if (!refunded) {
                revert NativeFeeRefundFailed();
            }
        }

        emit FundsReleased(
            settlementId,
            settlement.payee,
            paidAmount,
            refundAmount
        );
        emit RealCrossChainFundsReleased(
            settlementId,
            settlement.payee,
            paidAmount,
            refundAmount,
            messageId,
            nativeFee
        );
    }

    function confirmCrossChainRelease(
        bytes32 settlementId,
        bytes32 messageId
    ) external {
        Settlement storage settlement = settlements[settlementId];

        if (settlement.status == Status.None) {
            revert SettlementNotFound(settlementId);
        }
        if (settlement.status != Status.ReleasePending) {
            revert InvalidStatus(settlementId, settlement.status);
        }
        if (msg.sender != platformSigner) {
            revert Unauthorized();
        }

        bytes32 expectedMessageId = releaseMessageIds[settlementId];
        if (expectedMessageId == bytes32(0) || expectedMessageId != messageId) {
            revert InvalidMessageId(expectedMessageId, messageId);
        }

        settlement.status = Status.Released;
        emit CrossChainReleaseConfirmed(settlementId, messageId, msg.sender);
    }

    function refund(bytes32 settlementId) external {
        Settlement storage settlement = settlements[settlementId];

        if (settlement.status == Status.None) {
            revert SettlementNotFound(settlementId);
        }
        if (settlement.status != Status.Locked) {
            revert InvalidStatus(settlementId, settlement.status);
        }
        if (msg.sender != settlement.payer && msg.sender != platformSigner) {
            revert Unauthorized();
        }

        settlement.status = Status.Refunded;

        settlement.token.safeTransfer(settlement.payer, settlement.budgetAmount);

        emit FundsRefunded(
            settlementId,
            settlement.payer,
            settlement.budgetAmount
        );
    }
}
