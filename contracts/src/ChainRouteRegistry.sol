// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

contract ChainRouteRegistry {
    struct Route {
        bytes32 routeKey;
        uint32 routeVersion;
        uint64 sourceChainId;
        uint64 destinationChainId;
        address sourceGateway;
        address remoteWorkBoard;
        address payoutReceiver;
        address token;
        bool active;
    }

    address public immutable controller;
    mapping(bytes32 routeKey => uint32 version) public latestVersion;
    mapping(bytes32 routeKey => mapping(uint32 version => Route route)) private routes;

    event RouteRegistered(
        bytes32 indexed routeKey,
        uint32 indexed routeVersion,
        uint64 indexed destinationChainId,
        address remoteWorkBoard,
        address payoutReceiver,
        address token
    );
    event RouteStatusChanged(bytes32 indexed routeKey, uint32 indexed routeVersion, bool active);

    error Unauthorized();
    error InvalidRoute();
    error RouteNotFound(bytes32 routeKey, uint32 routeVersion);

    constructor(address _controller) {
        if (_controller == address(0)) revert InvalidRoute();
        controller = _controller;
    }

    modifier onlyController() {
        if (msg.sender != controller) revert Unauthorized();
        _;
    }

    function registerRoute(
        bytes32 routeKey,
        uint64 sourceChainId,
        uint64 destinationChainId,
        address sourceGateway,
        address remoteWorkBoard,
        address payoutReceiver,
        address token
    ) external onlyController returns (uint32 routeVersion) {
        if (
            routeKey == bytes32(0) ||
            sourceChainId == 0 ||
            destinationChainId == 0 ||
            sourceChainId == destinationChainId ||
            sourceGateway == address(0) ||
            remoteWorkBoard == address(0) ||
            payoutReceiver == address(0) ||
            token == address(0)
        ) revert InvalidRoute();

        routeVersion = latestVersion[routeKey] + 1;
        latestVersion[routeKey] = routeVersion;
        routes[routeKey][routeVersion] = Route({
            routeKey: routeKey,
            routeVersion: routeVersion,
            sourceChainId: sourceChainId,
            destinationChainId: destinationChainId,
            sourceGateway: sourceGateway,
            remoteWorkBoard: remoteWorkBoard,
            payoutReceiver: payoutReceiver,
            token: token,
            active: true
        });

        emit RouteRegistered(
            routeKey,
            routeVersion,
            destinationChainId,
            remoteWorkBoard,
            payoutReceiver,
            token
        );
    }

    function setRouteActive(
        bytes32 routeKey,
        uint32 routeVersion,
        bool active
    ) external onlyController {
        Route storage route = routes[routeKey][routeVersion];
        if (route.routeVersion == 0) revert RouteNotFound(routeKey, routeVersion);
        route.active = active;
        emit RouteStatusChanged(routeKey, routeVersion, active);
    }

    function getRoute(bytes32 routeKey, uint32 routeVersion) external view returns (Route memory route) {
        route = routes[routeKey][routeVersion];
        if (route.routeVersion == 0) revert RouteNotFound(routeKey, routeVersion);
    }

    function getLatestActiveRoute(bytes32 routeKey) external view returns (Route memory route) {
        uint32 version = latestVersion[routeKey];
        while (version > 0) {
            route = routes[routeKey][version];
            if (route.active) return route;
            unchecked { version -= 1; }
        }
        revert RouteNotFound(routeKey, latestVersion[routeKey]);
    }
}
