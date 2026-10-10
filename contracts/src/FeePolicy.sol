// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

contract FeePolicy {
    enum FeeCategory {
        TaskOfferCcip,
        ResultProofCcip,
        ResultProofRelay,
        SettlementCcip,
        SourceConfirmRelay,
        AgentSuccessAllowance,
        PlatformFailureCompensation
    }

    struct Policy {
        uint256[7] caps;
        uint64 validUntil;
        bool active;
    }

    address public immutable controller;
    mapping(bytes32 routeKey => mapping(uint32 routeVersion => Policy policy)) private policies;

    event FeePolicyConfigured(bytes32 indexed routeKey, uint32 indexed routeVersion, uint64 validUntil);
    event FeePolicyStatusChanged(bytes32 indexed routeKey, uint32 indexed routeVersion, bool active);

    error Unauthorized();
    error InvalidPolicy();
    error PolicyUnavailable(bytes32 routeKey, uint32 routeVersion);
    error FeeCapExceeded(uint256 cap, uint256 requested);

    constructor(address _controller) {
        if (_controller == address(0)) revert InvalidPolicy();
        controller = _controller;
    }

    modifier onlyController() {
        if (msg.sender != controller) revert Unauthorized();
        _;
    }

    function configurePolicy(
        bytes32 routeKey,
        uint32 routeVersion,
        uint256[7] calldata caps,
        uint64 validUntil
    ) external onlyController {
        if (routeKey == bytes32(0) || routeVersion == 0 || validUntil <= block.timestamp) revert InvalidPolicy();
        policies[routeKey][routeVersion] = Policy({caps: caps, validUntil: validUntil, active: true});
        emit FeePolicyConfigured(routeKey, routeVersion, validUntil);
    }

    function setPolicyActive(bytes32 routeKey, uint32 routeVersion, bool active) external onlyController {
        Policy storage policy = policies[routeKey][routeVersion];
        if (policy.validUntil == 0) revert PolicyUnavailable(routeKey, routeVersion);
        policy.active = active;
        emit FeePolicyStatusChanged(routeKey, routeVersion, active);
    }

    function maxFee(bytes32 routeKey, uint32 routeVersion, FeeCategory category) public view returns (uint256) {
        Policy storage policy = policies[routeKey][routeVersion];
        if (!policy.active || policy.validUntil < block.timestamp) revert PolicyUnavailable(routeKey, routeVersion);
        return policy.caps[uint256(category)];
    }

    function validateFee(
        bytes32 routeKey,
        uint32 routeVersion,
        FeeCategory category,
        uint256 amount
    ) external view {
        uint256 cap = maxFee(routeKey, routeVersion, category);
        if (amount > cap) revert FeeCapExceeded(cap, amount);
    }
}
