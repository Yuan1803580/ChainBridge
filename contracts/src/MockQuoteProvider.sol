// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

contract MockQuoteProvider {
    uint256 public constant WAD = 1e18;
    uint16 public constant MAX_BPS = 10_000;

    struct Rate {
        uint8 sourceDecimals;
        uint8 settlementDecimals;
        uint256 settlementPerSourceWad;
        bool exists;
    }

    struct Quote {
        address sourceAsset;
        address settlementToken;
        uint256 sourceAmount;
        uint256 quotedSettlementAmount;
        uint256 minSettlementAmount;
        uint16 maxSlippageBps;
        uint256 quoteDeadline;
        bytes32 quoteHash;
    }

    mapping(bytes32 => Rate) public rates;

    event RateUpdated(
        address indexed sourceAsset,
        address indexed settlementToken,
        uint8 sourceDecimals,
        uint8 settlementDecimals,
        uint256 settlementPerSourceWad
    );

    error InvalidAsset();
    error InvalidAmount();
    error InvalidDecimals();
    error InvalidRate();
    error InvalidSlippage();
    error QuoteExpired(uint256 quoteDeadline, uint256 currentTimestamp);
    error RateNotFound(address sourceAsset, address settlementToken);

    function setRate(
        address sourceAsset,
        address settlementToken,
        uint8 sourceDecimals,
        uint8 settlementDecimals,
        uint256 settlementPerSourceWad
    ) external {
        if (sourceAsset == address(0) || settlementToken == address(0)) {
            revert InvalidAsset();
        }
        if (sourceDecimals > 77 || settlementDecimals > 77) {
            revert InvalidDecimals();
        }
        if (settlementPerSourceWad == 0) {
            revert InvalidRate();
        }

        rates[_pairKey(sourceAsset, settlementToken)] = Rate({
            sourceDecimals: sourceDecimals,
            settlementDecimals: settlementDecimals,
            settlementPerSourceWad: settlementPerSourceWad,
            exists: true
        });

        emit RateUpdated(
            sourceAsset,
            settlementToken,
            sourceDecimals,
            settlementDecimals,
            settlementPerSourceWad
        );
    }

    function quote(
        address sourceAsset,
        address settlementToken,
        uint256 sourceAmount,
        uint16 maxSlippageBps,
        uint256 quoteDeadline
    ) external view returns (Quote memory result) {
        if (sourceAsset == address(0) || settlementToken == address(0)) {
            revert InvalidAsset();
        }
        if (sourceAmount == 0) {
            revert InvalidAmount();
        }
        if (maxSlippageBps > MAX_BPS) {
            revert InvalidSlippage();
        }
        if (block.timestamp > quoteDeadline) {
            revert QuoteExpired(quoteDeadline, block.timestamp);
        }

        Rate memory rate = rates[_pairKey(sourceAsset, settlementToken)];
        if (!rate.exists) {
            revert RateNotFound(sourceAsset, settlementToken);
        }

        uint256 quotedSettlementAmount = (sourceAmount *
            rate.settlementPerSourceWad *
            (10 ** rate.settlementDecimals)) /
            WAD /
            (10 ** rate.sourceDecimals);
        uint256 minSettlementAmount = (quotedSettlementAmount *
            (MAX_BPS - maxSlippageBps)) / MAX_BPS;
        bytes32 quoteHash = hashQuote(
            sourceAsset,
            settlementToken,
            sourceAmount,
            quotedSettlementAmount,
            minSettlementAmount,
            maxSlippageBps,
            quoteDeadline
        );

        return
            Quote({
                sourceAsset: sourceAsset,
                settlementToken: settlementToken,
                sourceAmount: sourceAmount,
                quotedSettlementAmount: quotedSettlementAmount,
                minSettlementAmount: minSettlementAmount,
                maxSlippageBps: maxSlippageBps,
                quoteDeadline: quoteDeadline,
                quoteHash: quoteHash
            });
    }

    function hashQuote(
        address sourceAsset,
        address settlementToken,
        uint256 sourceAmount,
        uint256 quotedSettlementAmount,
        uint256 minSettlementAmount,
        uint16 maxSlippageBps,
        uint256 quoteDeadline
    ) public pure returns (bytes32) {
        return
            keccak256(
                abi.encode(
                    sourceAsset,
                    settlementToken,
                    sourceAmount,
                    quotedSettlementAmount,
                    minSettlementAmount,
                    maxSlippageBps,
                    quoteDeadline
                )
            );
    }

    function _pairKey(
        address sourceAsset,
        address settlementToken
    ) private pure returns (bytes32) {
        return keccak256(abi.encode(sourceAsset, settlementToken));
    }
}
