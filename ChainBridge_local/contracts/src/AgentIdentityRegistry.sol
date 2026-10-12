// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

contract AgentIdentityRegistry {
    struct AgentIdentity {
        bytes32 didHash;
        address wallet;
        uint64 homeChainId;
        bytes32 rolesHash;
        bytes32 capabilitiesHash;
        uint64 revision;
        bool active;
    }

    address public immutable controller;
    mapping(bytes32 didHash => AgentIdentity identity) public identities;
    mapping(address wallet => bytes32 didHash) public walletDidHashes;

    event AgentIdentityRegistered(
        bytes32 indexed didHash,
        address indexed wallet,
        uint64 indexed homeChainId,
        bytes32 rolesHash,
        bytes32 capabilitiesHash,
        uint64 revision,
        bool active
    );

    error Unauthorized();
    error InvalidInput();
    error WalletAlreadyBound(address wallet, bytes32 didHash);

    constructor(address _controller) {
        if (_controller == address(0)) revert InvalidInput();
        controller = _controller;
    }

    function registerOrUpdate(
        bytes32 didHash,
        address wallet,
        uint64 homeChainId,
        bytes32 rolesHash,
        bytes32 capabilitiesHash,
        bool active
    ) external {
        if (msg.sender != controller) revert Unauthorized();
        if (
            didHash == bytes32(0) ||
            wallet == address(0) ||
            homeChainId == 0 ||
            rolesHash == bytes32(0) ||
            capabilitiesHash == bytes32(0)
        ) revert InvalidInput();

        bytes32 boundDidHash = walletDidHashes[wallet];
        if (boundDidHash != bytes32(0) && boundDidHash != didHash && identities[boundDidHash].active) {
            revert WalletAlreadyBound(wallet, boundDidHash);
        }

        AgentIdentity storage previous = identities[didHash];
        if (previous.wallet != address(0) && previous.wallet != wallet) {
            delete walletDidHashes[previous.wallet];
        }
        uint64 revision = previous.revision + 1;
        identities[didHash] = AgentIdentity({
            didHash: didHash,
            wallet: wallet,
            homeChainId: homeChainId,
            rolesHash: rolesHash,
            capabilitiesHash: capabilitiesHash,
            revision: revision,
            active: active
        });
        walletDidHashes[wallet] = didHash;
        emit AgentIdentityRegistered(didHash, wallet, homeChainId, rolesHash, capabilitiesHash, revision, active);
    }

    function isActiveAgent(bytes32 didHash, address wallet, uint64 homeChainId) external view returns (bool) {
        AgentIdentity storage identity = identities[didHash];
        return
            identity.active &&
            identity.wallet == wallet &&
            identity.homeChainId == homeChainId &&
            walletDidHashes[wallet] == didHash;
    }
}
