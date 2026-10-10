// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

contract FailureClassifier {
    enum ResponsibleParty { None, Agent, Platform, Route, User, Disputed }
    enum GasTreatment { None, AgentDeposit, PlatformTreasury, UserSourceDeposit, HoldForDispute }

    struct Rule {
        ResponsibleParty responsibleParty;
        GasTreatment gasTreatment;
        bool retryable;
        bool agentFault;
        bool routePaused;
        bool configured;
    }

    address public immutable controller;
    mapping(bytes32 reasonCode => Rule rule) public rules;

    event FailureRuleConfigured(bytes32 indexed reasonCode, ResponsibleParty responsibleParty, GasTreatment gasTreatment, bool retryable, bool agentFault, bool routePaused);

    error Unauthorized();
    error InvalidInput();

    constructor(address _controller) {
        if (_controller == address(0)) revert InvalidInput();
        controller = _controller;
    }

    function configure(bytes32 reasonCode, ResponsibleParty party, GasTreatment treatment, bool retryable, bool agentFault, bool routePaused) external {
        if (msg.sender != controller) revert Unauthorized();
        if (reasonCode == bytes32(0) || party == ResponsibleParty.None || treatment == GasTreatment.None) revert InvalidInput();
        rules[reasonCode] = Rule(party, treatment, retryable, agentFault, routePaused, true);
        emit FailureRuleConfigured(reasonCode, party, treatment, retryable, agentFault, routePaused);
    }

    function classify(bytes32 reasonCode) external view returns (Rule memory rule) {
        rule = rules[reasonCode];
        if (!rule.configured) revert InvalidInput();
    }
}
